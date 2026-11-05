"""Agent turn execution: event stream, checkpoints, steering and abort.

Three responsibilities share this file because they share one object: the run.

- **The turn itself.** ``agent.iter`` keeps the run reachable, so an aborted or
  failed turn still hands back what was completed and the session keeps its
  context.
- **Steering.** The prompt stays usable while the agent works. A message typed
  mid-turn is queued with ``run.enqueue``, which delivers it at the earliest real
  seam: right after the tool results the agent is waiting on, or as one more turn
  when the agent was about to finish. The model reads it as a new instruction,
  which is the point: correcting course mid-flight is cheaper than restarting.
- **Aborting.** The first ``Esc`` asks for confirmation in the status bar and
  waits three seconds; the second one cancels the run and the shell commands it
  spawned, and the history is repaired so the next turn starts from a
  conversation a provider accepts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.capabilities import ProcessEventStream
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import (
    EnqueuedMessagesEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    repair_messages,
)
from pydantic_ai.usage import UsageLimits
from textual import work
from textual.css.query import NoMatches
from textual.widgets import Static

from src.engine.agents.step_limiter import StepLimiter, StepLimitExceeded
from src.engine.providers.domain.thinking import settings_value as thinking_settings
from src.engine.shell import running as running_commands
from src.ui.messages import ChatNotice
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.status_bar import StatusBar

CHECKPOINT_INTERVAL = 1.0
"""Minimum seconds between checkpoints during a turn (the final save is mandatory)."""

ABORT_WINDOW = 3.0
"""Seconds the first `Esc` waits for the second one before giving up."""

ABORT_HINT = 'Press Esc again to stop the agent.'
"""What the status bar asks while the abort window is open."""

STOPPING_HINT = 'Stopping the agent…'
"""What the status bar says between the confirmation and the run unwinding."""


@dataclass
class Steer:
    """A message typed while a turn runs, waiting to be delivered to it.

    ``enqueue_id`` is ``None`` for the few milliseconds between the submit and
    the run being ready: the message is already accepted, and it is handed to the
    run as soon as it exists (see ``_flush_steering``).
    """

    text: str
    widget: Static | None = None
    enqueue_id: str | None = None
    delivered: bool = field(default=False)


class TurnMixin:
    """Runs the agent turn against the app.

    It expects the app to define ``_agent``, ``_history``, ``_session``, ``_store``,
    ``_current_run``, ``_last_checkpoint_at``, ``_turn_active``, ``_turn_session``,
    ``_steering``, ``_abort_deadline`` and ``_abort_timer``.
    """

    _agent: Any
    _history: list[Any]
    _session: Any
    _store: Any
    _current_run: Any
    _last_checkpoint_at: float
    _settings: Any
    _turn_active: bool
    _turn_session: Any
    _steering: list[Steer]
    _abort_deadline: float | None
    _abort_timer: Any

    def model_settings(self) -> dict[str, Any] | None:
        """Per-request settings: the reasoning level of the active model.

        `default` sends nothing so the model keeps its own behaviour; `off` maps
        to `False` (the lowest effort a model accepts, or reasoning disabled
        where the route allows it); a level goes as is.
        """
        service = getattr(self, '_model_service', None)
        if service is None:
            return None
        value = thinking_settings(service.snapshot().thinking)
        return None if value is None else {'thinking': value}

    def checkpoint(self, *, force: bool = False) -> None:
        """Persists the progress of the current turn, at most once per second.

        It is called after every agent iteration and every stream
        event. If the process dies halfway through a long turn, the session on
        disk keeps what was completed up to the last checkpoint. It only writes
        when the history changed and the throttle window passed; the final
        save of the turn is mandatory and does not go through here.
        """
        run = self._current_run
        if run is None:
            return
        messages = run.all_messages()
        if not messages:
            return
        # The session the turn started on, not "the current one": a turn must
        # not write its progress into a session opened while it ran.
        session = self._turn_session or self._session
        current = session.messages
        if len(messages) == len(current) and current and messages[-1] is current[-1]:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint_at < CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint_at = now
        session.messages = list(messages)
        self._store.save(session)
        # The context section updates per step, not only when the turn closes.
        self.refresh_context(messages)
        # The debug view too (it is a no-op if closed).
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug(messages)

    def turn_handler(self, limiter: StepLimiter, chat: ChatLog) -> Any:
        """Stream handler that feeds the chat and the checkpoints."""

        async def handler(_ctx: Any, events: Any) -> None:
            async for event in events:
                limiter.count(event)
                # Checkpoint per event: as soon as the run adds a message to
                # the history (e.g. the user prompt) it lands on disk.
                self.checkpoint()
                await self.dispatch_event(event, chat)

        return handler

    async def dispatch_event(self, event: Any, chat: ChatLog) -> None:
        """Translates a stream event into what the chat shows."""
        if isinstance(event, PartStartEvent):
            if event.index == 0:
                await chat.begin_response()
            part = event.part
            if isinstance(part, ThinkingPart) and part.content:
                await chat.add_thinking(part.content)
            elif isinstance(part, TextPart) and part.content:
                await chat.add_text(part.content)
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and delta.content_delta:
                await chat.add_text(delta.content_delta)
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                await chat.add_thinking(delta.content_delta)
        elif isinstance(event, FunctionToolResultEvent):
            ok = getattr(event.part, 'outcome', 'success') == 'success'
            await chat.add_tool_result(event.part.tool_name, event.part.content, ok=ok)
        elif isinstance(event, EnqueuedMessagesEvent):
            # The run just picked up a message typed mid-turn: its line stops
            # saying that it is waiting.
            self._mark_delivered(event.enqueue_id, chat)

    # --- steering ----------------------------------------------------------

    def steer(self, text: str, widget: Static | None = None) -> None:
        """Hands a message typed mid-turn to the running turn.

        It goes through ``enqueue`` because that is the only path that keeps the
        run valid: the message becomes a real ``ModelRequest`` in the history,
        placed where the agent can read it without the pending tool calls losing
        their answers.
        """
        entry = Steer(text=text, widget=widget)
        run = self._current_run
        if run is None:
            # The turn was accepted but the run is not up yet. The message waits
            # here and `_flush_steering` delivers it as soon as it is.
            self._steering.append(entry)
            return
        self._enqueue(entry, run)

    def _enqueue(self, entry: Steer, run: Any) -> None:
        """Queues one message on a live run."""
        try:
            entry.enqueue_id = run.enqueue(entry.text)
        except Exception as exc:  # noqa: BLE001 - a refused message must not be lost
            self.post_message(ChatNotice(f'Could not send it: {exc}', style='bold red'))
            self._steering.append(entry)
            return
        if entry.enqueue_id is not None:
            self._steering.append(entry)

    def _flush_steering(self, run: Any) -> None:
        """Delivers what was typed before the run existed."""
        waiting = [entry for entry in self._steering if entry.enqueue_id is None]
        for entry in waiting:
            self._steering.remove(entry)
            self._enqueue(entry, run)

    def _mark_delivered(self, enqueue_id: str, chat: ChatLog) -> None:
        """Drops the "queued" mark from the line the run just injected."""
        for entry in self._steering:
            if entry.enqueue_id != enqueue_id:
                continue
            entry.delivered = True
            if entry.widget is not None:
                chat.mark_delivered(entry.widget)
            return

    def _take_undelivered(self) -> list[Steer]:
        """Whatever was never delivered, so it can go back to the prompt.

        A message typed in the last instant of a turn can miss its seam. It is
        not dropped: it goes back to the prompt so the next `Enter` sends it.
        """
        leftovers = [entry for entry in self._steering if not entry.delivered]
        self._steering.clear()
        return leftovers

    # --- abort -------------------------------------------------------------

    def request_abort(self) -> bool:
        """`Esc`: the first press asks, the second one inside the window confirms.

        Returns True when the press is the confirmation; the caller then decides
        what stopping means (`abort_turn`, in both the prompt and the app).

        Stopping throws away what the agent was writing, so it takes two presses
        and a three-second window. Anything else pressed in between (or the
        window expiring) calls it off: a confirmation that survives other typing
        is a confirmation by accident.
        """
        if not self._turn_active:
            return False
        if self._abort_deadline is not None and time.monotonic() < self._abort_deadline:
            return True
        self._abort_deadline = time.monotonic() + ABORT_WINDOW
        self._paint_status(ABORT_HINT, style='bold yellow')
        self._abort_timer = self.set_timer(ABORT_WINDOW, self._expire_abort)
        return False

    def cancel_abort(self) -> None:
        """Any key other than `Esc` drops the pending confirmation."""
        if self._abort_deadline is None:
            return
        self._abort_deadline = None
        self._stop_abort_timer()
        self._paint_status('')

    def _expire_abort(self) -> None:
        """The three seconds passed: nothing is going to be stopped."""
        self._abort_timer = None
        self._abort_deadline = None
        self._paint_status('')

    def _stop_abort_timer(self) -> None:
        timer = getattr(self, '_abort_timer', None)
        if timer is not None:
            timer.stop()
            self._abort_timer = None

    def abort_turn(self) -> None:
        """Stops the run where it is and kills what it spawned.

        Order matters: the shell commands die first, by process group, so a
        command that started children leaves nothing behind; then the run is
        cancelled so the graph unwinds instead of waiting for a tool result that
        is never coming.

        The history is not thrown away. `RunCancelled` carries what the run
        completed and `repair_messages` closes the calls that were cut off
        mid-flight, so the next message starts from a valid conversation instead
        of tripping over an unanswered call.
        """
        self._abort_deadline = None
        self._stop_abort_timer()
        self._paint_status(STOPPING_HINT, style='yellow')
        running_commands.kill_all()
        run = self._current_run
        if run is None:
            return
        try:
            run.cancel()
        except Exception as exc:  # noqa: BLE001 - a run that already ended is not an error
            # Cancelling twice, or cancelling a run the graph already closed, is
            # a no-op for the user: the turn was ending anyway.
            self.log.debug(f'run.cancel() ignored: {exc}')

    # --- the turn ----------------------------------------------------------

    def start_turn(self, text: str) -> None:
        """Accepts a new turn.

        The flag is set here and not inside the worker: the worker does not start
        until the event loop gets to it, and a message typed in between has to
        know that a turn is already on its way.
        """
        if self._turn_active:
            self.steer(text)
            return
        self._turn_active = True
        self.run_turn(text)

    @work(exclusive=True)
    async def run_turn(self, text: str) -> None:
        """Runs a full turn, persisting progress along the way."""
        self._turn_active = True
        # The session this turn belongs to. A command that swaps it mid-turn is
        # refused (`CommandsMixin`), but the run must not take that on trust: if
        # the session changed underneath, what this turn produced belongs to the
        # old one, and writing it into the new one silently overwrites a
        # conversation the user just loaded.
        session = self._session
        chat = self.query_one(ChatLog)
        limiter = StepLimiter(self._settings.max_steps)
        handler = self.turn_handler(limiter, chat)
        # `agent.iter` (instead of `agent.run`) keeps the run accessible: if the
        # turn is aborted by the step limit or by an error, `run.all_messages()`
        # returns what was completed and the session does not lose the context.
        #
        # `request_limit=None` disables the 50-request cap pydantic-ai
        # applies by default: Sigil's step limit (`SIGIL_MAX_STEPS`) already
        # bounds the turn, and the default would cut earlier with a `UsageLimitExceeded`
        # the user did not configure.
        run: Any = None
        history = self._history
        # Where `checkpoint` writes: the session this turn started on.
        self._turn_session = session
        # The first checkpoint of the turn (the user prompt) is saved right away.
        self._last_checkpoint_at = 0.0
        try:
            async with self._agent.iter(
                text,
                message_history=self._history,
                capabilities=[ProcessEventStream(handler=handler)],
                model_settings=self.model_settings(),
                usage_limits=UsageLimits(request_limit=None),
            ) as run:
                self._current_run = run
                # A message typed while the run was being built has no id yet.
                self._flush_steering(run)
                async for _node in run:
                    # Checkpoint per iteration: every completed node (model
                    # response, tool batch) gets persisted.
                    self.checkpoint()
            history = run.all_messages()
        except StepLimitExceeded:
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(
                f'Step limit of {self._settings.max_steps} reached; turn aborted.',
                style='bold red',
            )
        except RunCancelled:
            # The user stopped the turn: cancelling an `ask_user` panel, or `Esc`
            # twice. It is not an error, so there is no error to report. What the
            # run completed is kept, with the dangling tool calls closed out so
            # the next message does not trip over an unanswered call.
            if run is not None:
                history = repair_messages(run.all_messages())
            await chat.add_notice('Turn cancelled.', style='yellow')
        except Exception as exc:  # noqa: BLE001
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(f'Error: {exc}', style='bold red')
        finally:
            self._current_run = None
            self._turn_session = None
            self._turn_active = False
            self._abort_deadline = None
            self._stop_abort_timer()
            self._paint_status('')
            chat.finalize()

        # Written to the session the turn started on. If it is no longer the
        # active one, the turn's result is still saved where it belongs and the
        # conversation on screen is left alone.
        session.messages = history
        self._store.save(session)
        if session is self._session:
            self._history = history
        # A message that missed the last seam is not lost: it comes back to the
        # prompt, ready to be sent again. The chat line goes with it, so what is
        # left on screen matches what the agent actually read.
        leftovers = self._take_undelivered()
        prompt = self.query_one(PromptInput)
        if leftovers:
            prompt.value = '\n'.join(entry.text for entry in leftovers)
            for entry in leftovers:
                if entry.widget is not None:
                    await entry.widget.remove()
            await chat.add_notice(
                'The turn ended before that message was read; it is back in the prompt.',
                style='yellow',
            )
        prompt.focus()
        self.refresh_sidebar()
        # The debug view shows the cost of the turn just closed.
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug()

    # --- status bar --------------------------------------------------------

    def _paint_status(self, text: str, *, style: str = 'yellow') -> None:
        try:
            bar = self.query_one(StatusBar)
        except NoMatches:
            # The bar may not be composed yet (a turn started from code before
            # mount). Its absence is not worth failing a turn over.
            return
        if text:
            bar.show(text, style=style)
        else:
            bar.clear()
