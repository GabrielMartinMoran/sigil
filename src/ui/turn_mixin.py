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
- **Surviving a crash.** The history is checkpointed as the turn advances, so a
  process that dies mid-turn leaves a session whose last message can be a tool
  call with no result behind it. That history is repaired before it reaches the
  agent (see ``src.engine.sessions.recovery``), because pydantic-ai refuses a new
  prompt on top of it — permanently.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent
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
    ToolCallPart,
)
from pydantic_ai.usage import UsageLimits
from textual import work
from textual.css.query import NoMatches
from textual.widgets import Static

from src.engine.agents.step_budget import StepBudget
from src.engine.attachments.content import user_content
from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.providers.domain.thinking import settings_value as thinking_settings
from src.engine.sessions.recovery import recover_history
from src.ui.desktop_notifications import Attention
from src.ui.messages import ChatNotice
from src.ui.service_client import (
    OutgoingImage,
    PayloadTooLarge,
    RequestRejected,
    ServiceBusy,
    ServiceClient,
    ServiceClientError,
    ServiceUnavailable,
    TurnRequest,
    Unauthorized,
    UnexpectedResponse,
)
from src.ui.session_refresh import render_refreshed_session, session_view
from src.ui.turn_timing import TurnTimings
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from src.engine.agents.rollback import Rollback

CHECKPOINT_INTERVAL = 1.0
"""Minimum seconds between checkpoints during a turn (the final save is mandatory)."""

ABORT_WINDOW = 3.0
"""Seconds the first `Esc` waits for the second one before giving up."""

ABORT_HINT = 'Press Esc again to stop the agent.'
"""What the status bar asks while the abort window is open."""

STOPPING_HINT = 'Stopping the agent…'
"""What the status bar says between the confirmation and the run unwinding."""

SERVICE_SEND_GROUP = 'service-send'
"""Worker group of the request that hands one message to the service."""

SERVICE_STOP_GROUP = 'service-stop'
"""Worker group of the stop request."""


def pending_request(previous: TurnRequest | None, text: str, images: tuple[OutgoingImage, ...]) -> TurnRequest:
    """The request to put on the wire: the failed one again, or a new message.

    The durable queue deduplicates by request id, so the id belongs to the content and
    not to the attempt. Sending the same text and images after a refusal is the same
    message and reuses its id; changing either is a different message and must not,
    because one id for two bodies is a conflict the service refuses. The decision is
    made here, once, and nothing on the server second-guesses it.
    """
    if previous is not None and previous.text == text and previous.images == images:
        return previous
    return TurnRequest(text=text, images=images)


_BACK_IN_PROMPT = ' What you typed is back in the prompt.'
"""Said once, so the sentences above can end with it without repeating themselves."""

_BUSY_MARKERS = ('turn is running', 'turn is in progress', 'a turn is running')
"""How the service words a busy refusal, lowercased.

`/api/session/open` and `/api/rollback` answer `409` for other reasons too — a turn
this session cannot reach, a session that does not exist. The marker is what tells
those apart without matching a whole sentence that may be reworded.
"""


def _is_busy_reason(reason: str) -> bool:
    """Whether a `409` sentence is the service saying a turn is in the way."""
    text = reason.lower()
    return any(marker in text for marker in _BUSY_MARKERS)


def failure_notice(exc: ServiceClientError) -> str:
    """What the screen says about a refusal: the reason, then the way out.

    One function, because the difference between these failures is the whole reason
    the client raises typed ones: a terminal that printed them all the same way would
    have paid for the vocabulary and thrown it away. No sentence carries a traceback,
    and none stops at the reason — a person who cannot act on the message has learned
    nothing they can use.
    """
    if isinstance(exc, ServiceBusy):
        reason = str(exc).strip()
        if reason and not _is_busy_reason(reason):
            # Not every `409` means a turn is in the way: the routes use it for
            # anything the session cannot do *now*, including a turn it cannot
            # reach. Saying "still working" over that sentence would be inventing
            # a cause, so the server's own words are the answer.
            return f'The service refused this: {reason}'
        detail = f' The service said: {reason}' if reason else ''
        return (
            f'The agent is still working on this session.{detail} Wait for the turn to finish, or press Esc '
            'twice to stop it. What you typed is back in the prompt.'
        )
    if isinstance(exc, Unauthorized):
        return (
            'The service refused this terminal: its run token is not the one this service published. '
            'Restart Sigil so it attaches to the running service.'
        )
    if isinstance(exc, PayloadTooLarge):
        return (
            'That message is over the size the service accepts, images included. Remove an image or shorten '
            'the text, and send it again.'
        )
    if isinstance(exc, RequestRejected):
        return f'The service refused this message: {exc}'
    if isinstance(exc, ServiceUnavailable):
        if exc.launch_code == 'service_starting':
            return 'The service is still starting. Try again in a moment.' + _BACK_IN_PROMPT
        # The exception already opens with "No Sigil service answered at …": repeating
        # it here read as two failures instead of one.
        return f'{exc} Start it with `sigil server`, or check that it is still running.' + _BACK_IN_PROMPT
    if isinstance(exc, UnexpectedResponse):
        return (
            'The service answered something this terminal cannot read. Restart Sigil; if it happens again, '
            'the terminal and the service are different versions.'
        )
    return f'The service could not take this message: {exc}'


@dataclass
class Steer:
    """A message typed while a turn runs, waiting to be delivered to it.

    ``enqueue_id`` is ``None`` for the few milliseconds between the submit and
    the run being ready: the message is already accepted, and it is handed to the
    run as soon as it exists (see ``_flush_steering``).
    """

    text: str
    attachments: tuple[ImageAttachment, ...] = field(default=())
    widget: Static | None = None
    enqueue_id: str | None = None
    delivered: bool = field(default=False)

    def enqueued(self) -> tuple[str, ...]:
        """The message as ``enqueue`` wants it: text first, then each image.

        One argument per item, because ``enqueue`` gathers adjacent content into a
        single ``UserPromptPart``. A list passed as one argument is a list, not a
        multimodal prompt.
        """
        if not self.attachments:
            return (self.text,)
        ordered = sorted(self.attachments, key=lambda attachment: attachment.number)
        return (self.text, *(attachment.to_content() for attachment in ordered))


class TurnMixin:
    """Runs the agent turn against the app.

    It expects the app to define ``_agent``, ``_history``, ``_session``, ``_store``,
    ``_current_run``, ``_last_checkpoint_at``, ``_turn_active``, ``_turn_session``,
    ``_steering``, ``_abort_deadline`` and ``_abort_timer``.

    **Two transports, one of them test-only.** When the terminal is attached to the
    service, the turn belongs to that process: this mixin sends a message, paints
    what the stream reports and asks the service to stop, and never iterates the
    agent. The in-process path below is the seam the existing app tests mount
    (`SigilApp(agent=..., store=...)`); `src/sigil.py` — the only production entry
    point — cannot reach it, because attaching is the only thing it builds. Nothing
    may grow here that both paths need: the moment a decision is duplicated is the
    moment the two disagree.
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
    _client: ServiceClient | None
    _service_request: TurnRequest | None
    _service_steers: list[Static]
    _service_sent: set[str]

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
        # The measurement of the blocks that are always sent rides with the row, so a
        # session loaded later knows its real budget instead of starting at zero.
        self._meter.stamp(session)
        self._store.save_progress(session)
        # The context section updates per step, not only when the turn closes.
        self.refresh_context(messages)
        # The debug view too (it is a no-op if closed).
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug(messages)

    def turn_handler(self, chat: ChatLog) -> Any:
        """Stream handler that feeds the chat and the checkpoints."""

        async def handler(_ctx: Any, events: Any) -> None:
            async for event in events:
                timings = getattr(self, '_turn_timings', None)
                if timings is not None:
                    timings.stream_event()
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
                self.set_activity_label('Thinking')
            part = event.part
            if isinstance(part, ThinkingPart):
                await chat.add_thinking(part.content)
            elif isinstance(part, TextPart) and part.content:
                await chat.add_text(part.content)
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and delta.content_delta:
                await chat.add_text(delta.content_delta)
                self.set_activity_label('Writing')
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                await chat.add_thinking(delta.content_delta)
                self.set_activity_label('Thinking')
        elif isinstance(event, FunctionToolResultEvent):
            ok = getattr(event.part, 'outcome', 'success') == 'success'
            run = getattr(self, '_current_run', None)
            args = None
            if run is not None:
                for message in reversed(run.all_messages()):
                    call = next(
                        (
                            part
                            for part in message.parts
                            if isinstance(part, ToolCallPart) and part.tool_call_id == event.part.tool_call_id
                        ),
                        None,
                    )
                    if call is not None:
                        args = call.args
                        break
            await chat.add_tool_result(event.part.tool_name, event.part.content, ok=ok, args=args)
            self.set_activity_label('Thinking')
        elif isinstance(event, EnqueuedMessagesEvent):
            # The run just picked up a message typed mid-turn: its line stops
            # saying that it is waiting.
            self._mark_delivered(event.enqueue_id, chat)

    # --- rollback ----------------------------------------------------------

    def snapshot_workspace(self) -> None:
        """Freezes the workspace into the checkpoint of the turn that just started.

        It is the only part of a checkpoint that costs milliseconds — ~7 ms on this
        repository's 444 files, ~100 ms on a two-thousand-file tree — so it runs in a
        worker thread and never on the checkpoint path, which fires once per second.
        The row already exists: this fills its `tree_commit`.
        """
        rollback = getattr(self, '_rollback', None)
        session = getattr(self, '_session', None)
        if rollback is None or session is None:
            return
        self.run_worker(self._snapshot_workspace(rollback, session.id))

    async def _snapshot_workspace(self, rollback: Rollback, session_id: str) -> None:
        import asyncio

        try:
            await asyncio.to_thread(_take_snapshot, rollback, session_id)
        except Exception:  # noqa: BLE001, S110 - a snapshot must not break a turn
            # Losing the snapshot costs the files half of a future rollback, not the
            # turn the user is waiting for. The conversation half still works.
            pass

    # --- steering ----------------------------------------------------------

    def steer(
        self,
        text: str,
        widget: Static | None = None,
        *,
        attachments: tuple[ImageAttachment, ...] = (),
    ) -> None:
        """Hands a message typed mid-turn to the running turn.

        It goes through ``enqueue`` because that is the only path that keeps the
        run valid: the message becomes a real ``ModelRequest`` in the history,
        placed where the agent can read it without the pending tool calls losing
        their answers.

        An image pasted mid-turn travels the same way: ``enqueue`` takes
        multimodal content, so ``[Image #1]`` in the text and the picture beside
        it arrive together in the request the agent reads next.
        """
        if self._client is not None:
            self.submit_to_service(text, attachments=attachments, widget=widget)
            return
        entry = Steer(text=text, attachments=attachments, widget=widget)
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
            # Spread, not the list itself: `enqueue` takes one item per argument,
            # and a list passed as one item is not multimodal content.
            entry.enqueue_id = run.enqueue(*entry.enqueued())
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
        if self._client is not None:
            # The shell commands and the subagents are the service's: it stops what
            # it started and reports when the turn is over.
            self.stop_service_turn()
            return
        self._cancel_shell()
        # A subagent started by this turn has no reason to outlive it: `Esc` twice
        # means stop, and a background node still spending tokens is not stopped.
        self.stop_subagents()
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

    def start_turn(self, text: str, *, attachments: tuple[ImageAttachment, ...] = ()) -> None:
        """Accepts a new turn.

        The flag is set here and not inside the worker: the worker does not start
        until the event loop gets to it, and a message typed in between has to
        know that a turn is already on its way.
        """
        if self._client is not None:
            self.submit_to_service(text, attachments=attachments)
            return
        if self._turn_active:
            self.steer(text, attachments=attachments)
            return
        self._turn_active = True
        self.snapshot_workspace()
        # A session is named after the first thing it was asked to do. On the
        # worker, so the turn starts immediately: the title arrives when it is
        # ready, and the window title is repainted then.
        self.name_session(self._session, text)
        self.run_turn(text, attachments=attachments)

    # --- the service ---------------------------------------------------------

    def attached(self) -> bool:
        """Whether the conversation runs in the service instead of this process."""
        return self._client is not None

    @work(group=SERVICE_SEND_GROUP)
    async def submit_to_service(
        self, text: str, *, attachments: tuple[ImageAttachment, ...] = (), widget: Static | None = None
    ) -> None:
        """Hands one message to the service, and keeps it if the service refuses.

        One call for both a new turn and a steer: which of the two it is belongs to
        the process that owns the queue, and a terminal that guessed would sooner or
        later send a second turn into a running one.

        A refusal never costs what was typed. The message goes back into the composer
        with its images, in the same order, and the failure is one sentence that says
        what to do — that is the whole of the error policy here.
        """
        images = tuple(
            OutgoingImage(media_type=attachment.media_type, data=base64.b64encode(attachment.data).decode('ascii'))
            for attachment in sorted(attachments, key=lambda item: item.number)
        )
        request = pending_request(self._service_request, text, images)
        self._service_request = request
        try:
            await self._client.send_turn(request)
        except ServiceClientError as exc:
            await self.refuse_message(exc, request, attachments)
            return
        self._service_request = None
        # This window wrote this row, and its line is already in the transcript as
        # queued. The acceptance the service publishes for it must not be painted a
        # second time by the queue overlay (`refresh_service_queue`).
        self._service_sent.add(request.request_id)
        if widget is not None:
            # The service confirms it when the run reads it; until then the line says
            # it is waiting.
            self._service_steers.append(widget)
        self._turn_active = True
        self.set_activity(running=True, label='Thinking')

    async def refuse_message(
        self, exc: ServiceClientError, request: TurnRequest, attachments: tuple[ImageAttachment, ...]
    ) -> None:
        """One line about the refusal, and the message back where it was typed."""
        message = failure_notice(exc)
        await self.query_one(ChatLog).add_notice(f'✖ {message}', style='bold red')
        self.notify(message, severity='error')
        if not isinstance(exc, ServiceBusy):
            # A busy service is still running a turn; anything else means nothing is.
            self._turn_active = False
            self.set_activity(running=False)
        prompt = self.query_one(PromptInput)
        prompt.restore([(request.text, attachments)])
        prompt.focus()

    @work(group=SERVICE_STOP_GROUP)
    async def stop_service_turn(self) -> None:
        """Asks the service to stop the turn at the next safe point.

        The shell commands and the subagents belong to the service, so nothing here
        kills them: the service decides what stopping means and says when it stopped.
        """
        try:
            stopped = await self._client.abort()
        except ServiceClientError as exc:
            await self.query_one(ChatLog).add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            stopped = False
        if not stopped:
            # Nothing was running: the hint goes away instead of sitting there.
            self._paint_status('')

    @work(exclusive=True)
    async def run_turn(self, text: str, *, attachments: tuple[ImageAttachment, ...] = ()) -> None:
        """Runs a full turn, persisting progress along the way."""
        self._turn_active = True
        timings = self._turn_timings = TurnTimings()
        # The session this turn belongs to. A command that swaps it mid-turn is
        # refused (`CommandsMixin`), but the run must not take that on trust: if
        # the session changed underneath, what this turn produced belongs to the
        # old one, and writing it into the new one silently overwrites a
        # conversation the user just loaded.
        session = self._session
        chat = self.query_one(ChatLog)
        # What a subagent sent while nothing was running: it travels with this
        # turn's prompt, before what the user typed, because it is the answer to
        # something the agent did earlier.
        pending = self._take_subagent_messages()
        if pending:
            text = '\n\n'.join([*pending, text])
            if self._wake_pending:
                # The turn exists because of these reports: the agent should not
                # read them as something the user typed.
                self._wake_pending = False
                await chat.add_notice(
                    f'↩ Woken by {len(pending)} subagent report(s). They go with this turn.',
                    style='cyan',
                )
            else:
                await chat.add_notice(
                    f'{len(pending)} message(s) from subagents arrived while the session was idle; '
                    'they go with this turn.',
                    style='dim',
                )
        # The turn's budget, in the same shape a subagent gets one: when the steps
        # run out, the tools are taken away and the model is asked for a last answer
        # instead of the turn dying with nothing to show. The user keeps a complete
        # report of where the agent got, and the next message continues from there.
        #
        # The profile decides first and the environment is the fallback: a profile
        # that declares `max_steps` is saying how long *it* wants to work, and it is
        # the same key a subagent uses — one rule for both kinds of agent.
        budget = StepBudget(max_steps=self._turn_budget(), role='turn')
        handler = self.turn_handler(chat)
        # The bar says what is happening from the first second: a turn that looks
        # like an idle app is a turn the user interrupts or repeats. The prompt
        # stays usable (see `steer`), so nothing is disabled here.
        self.set_activity(running=True, label='Thinking')
        clock = self.set_interval(1.0, self.activity_tick)
        # `agent.iter` (instead of `agent.run`) keeps the run accessible: if the
        # turn is aborted by the step limit or by an error, `run.all_messages()`
        # returns what was completed and the session does not lose the context.
        #
        # `request_limit=None` disables the 50-request cap pydantic-ai
        # applies by default: Sigil's step limit (`SIGIL_MAX_STEPS`) already
        # bounds the turn, and the default would cut earlier with a `UsageLimitExceeded`
        # the user did not configure.
        run: Any = None
        # The history of the session this turn belongs to, not "the current one":
        # a run started before a session swap must continue the conversation it was
        # started from.
        history = session.messages
        attention: Attention | None = Attention.FINISHED
        # Where `checkpoint` writes: the session this turn started on.
        self._turn_session = session
        # The first checkpoint of the turn (the user prompt) is saved right away.
        self._last_checkpoint_at = 0.0
        try:
            # A process that died mid-turn leaves the last checkpoint *inside* a
            # tool batch: a response that asked for tools and no results, because the
            # results never came. pydantic-ai refuses a new prompt on top of that, and
            # it refuses it on every message after it, so the history is repaired here
            # — before it reaches the agent, not after the refusal. A healthy history
            # comes back untouched.
            recovery = recover_history(history)
            if recovery.changed:
                history = recovery.messages
                session.messages = history
                if session is self._session:
                    self._history = history
                self._store.save_progress(session)
                await chat.add_notice(recovery.notice, style='yellow')
            async with self._agent.iter(
                user_content(text, attachments),
                message_history=history,
                capabilities=[ProcessEventStream(handler=handler), budget],
                model_settings=self.model_settings(),
                usage_limits=UsageLimits(request_limit=None),
            ) as run:
                self._current_run = run
                # A message typed while the run was being built has no id yet.
                self._flush_steering(run)
                async for _node in timings.trace(run):
                    if Agent.is_model_request_node(_node):
                        self.set_activity_label('Waiting for model')
                    # Checkpoint per iteration: every completed node (model
                    # response, tool batch) gets persisted.
                    self.checkpoint()
            history = run.all_messages()
        except RunCancelled:
            attention = None
            # The user stopped the turn: cancelling an `ask_user` panel, or `Esc`
            # twice. It is not an error, so there is no error to report. What the
            # run completed is kept; the calls that were cut off are closed out
            # below, with the rest of the turn's tails.
            if run is not None:
                history = run.all_messages()
            await chat.add_notice('Turn cancelled.', style='yellow')
        except Exception as exc:  # noqa: BLE001
            attention = Attention.FAILED
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(f'Error: {exc}', style='bold red')
            self.notify(f'Turn failed: {exc}', severity='error')
        finally:
            clock.stop()
            self._current_run = None
            self.set_activity(running=False)
            self._turn_session = None
            self._turn_active = False
            self._abort_deadline = None
            self._stop_abort_timer()
            self._paint_status('')
            chat.finalize()

        # The end of the turn is repaired, on every path. A cancelled turn leaves a
        # call without a result, and so does a turn that died on an exception while a
        # tool ran; a step limit leaves a response nothing acted on. The next message
        # would be refused on any of them, and the save below would write the damage
        # to disk. It is a no-op for a turn that ended cleanly.
        history = recover_history(history).messages
        # Written to the session the turn started on. If it is no longer the
        # active one, the turn's result is still saved where it belongs and the
        # conversation on screen is left alone.
        session.messages = history
        self._meter.stamp(session)
        current = self._store.save_current(session)
        if session is self._session:
            if current is not session:
                await render_refreshed_session(session_view(self), current, chat)
                await chat.add_notice(
                    'Session changed elsewhere. Local progress is saved in a recovered session; reloaded the latest version.',
                    style='yellow',
                )
            self._history = current.messages
        # A message that missed the last seam is not lost: it comes back to the
        # prompt, ready to be sent again. The chat line goes with it, so what is
        # left on screen matches what the agent actually read.
        leftovers = self._take_undelivered()
        prompt = self.query_one(PromptInput)
        if leftovers:
            # Text and images together: a message whose text came back without its
            # picture would point at an `[Image #1]` that no longer exists.
            prompt.restore([(entry.text, entry.attachments) for entry in leftovers])
            for entry in leftovers:
                if entry.widget is not None:
                    await entry.widget.remove()
            await chat.add_notice(
                'The turn ended before that message was read; it is back in the prompt.',
                style='yellow',
            )
        if budget.exhausted:
            await chat.add_notice(
                f'Step limit of {budget.max_steps} reached: the turn got one last call without tools '
                'to report what it had.',
                style='yellow',
            )
        prompt.focus()
        self.refresh_sidebar()
        # The turn just wrote files: the bar shows the tree as it is now, not as it
        # was when the last poll ran.
        self.refresh_diff()
        timings.finish()
        if attention is not None:
            # Not the end of the whole job: a subagent may still be working, and the
            # notification waits for it rather than calling the user back early.
            self.settle_attention(attention)
        # The debug view shows the cost of the turn just closed.
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug()

    def _turn_budget(self) -> int:
        """The steps this turn gets: the profile's own, or the configured default.

        `/agent` can switch the profile mid-session, and the budget follows it: a
        profile written to answer briefly is allowed to be brief.
        """
        profile = self.active_agent()
        declared = getattr(profile, 'max_steps', None)
        return declared if declared else self._settings.max_steps

    def _take_subagent_messages(self) -> list[str]:
        """Messages waiting in the summoner, if the app has one."""
        take = getattr(self, 'take_subagent_messages', None)
        return take() if callable(take) else []

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


def _take_snapshot(rollback: Rollback, session_id: str) -> None:
    """Takes the tree snapshot of the newest checkpoint of a session, if it has none."""
    database = rollback._db
    row = database.one(
        'SELECT id, turn_id FROM checkpoints WHERE session_id = ? AND tree_commit IS NULL '
        'AND turn_id IS NOT NULL ORDER BY at DESC LIMIT 1',
        (session_id,),
    )
    if row is None:
        return
    commit = rollback.snapshots.take(str(row['id']))
    if commit is None:
        return
    database.execute('UPDATE checkpoints SET tree_commit = ? WHERE id = ?', (commit, row['id']))
