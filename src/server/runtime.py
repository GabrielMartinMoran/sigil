"""The turn loop, headless: what the terminal client does with widgets, done with events.

The terminal and the web client run the same decisions and differ only in where the
output goes. That duplication already exists in the engine's design (`ui/turn_mixin.py`
is the terminal's copy); this module is the web's, and it is written to keep the two
recognisably the same:

- ``agent.iter`` instead of ``agent.run``, so a turn that is cancelled or fails still
  hands back what it completed and the session keeps its context.
- ``run.enqueue`` for steering: a message typed mid-turn becomes a real
  ``ModelRequest`` at the next seam instead of a second turn competing with the first.
- ``run.cancel`` for abort, with the shell commands killed by process group first.
- The history is checkpointed as the turn advances and repaired at the end, because a
  process that dies mid-turn leaves a tool call with no result behind it and
  pydantic-ai refuses every later prompt on that history.

What is different is the sink: everything that the terminal paints is published on
the event hub as a delta or a fact, and the browser rebuilds the same view. Nothing
here formats text for a human — that is the client's job.
"""

from __future__ import annotations

import asyncio
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
)
from pydantic_ai.usage import UsageLimits

from src.engine.agents.step_budget import StepBudget
from src.engine.attachments.content import user_content
from src.engine.bootstrap import Composition, model_settings
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.sessions.recovery import recover_history
from src.engine.shell import running as running_commands
from src.engine.tooling.outputs import present_output
from src.server.events import EventHub

CHECKPOINT_INTERVAL = 1.0
"""Minimum seconds between checkpoints during a turn. The final save is mandatory."""

PREVIEW_CHARS = 400
"""How much of a tool result travels in the event. The full text is in the transcript."""


@dataclass
class PendingQuestion:
    """A question asked by the agent, waiting for an answer from a browser."""

    id: str
    questions: list[QuestionItem]
    future: asyncio.Future[list[QuestionAnswer] | None] = field(
        default_factory=lambda: asyncio.get_event_loop().create_future()
    )


class ServerRuntime:
    """Drives one workspace for one browser session.

    The workspace is process state, not client state: `os.chdir` is global, so two
    browsers share the directory the tools read. That is the same model the terminal
    has (one process, one workspace), and it is the honest one until the engine stops
    resolving paths against the process cwd.
    """

    def __init__(self, composition: Composition, hub: EventHub) -> None:
        self.composition = composition
        self.hub = hub
        composition.extension_notice = lambda text: hub.publish('notice', text=text, tone='danger')
        self._task: asyncio.Task[None] | None = None
        self._run: Any = None
        self._pending_question: PendingQuestion | None = None
        self._question_ids = 0
        self._last_checkpoint_at = 0.0
        self._turn_session: Any = None

        # The agent asks through the hub; the browser answers over HTTP.
        composition.question_hub.set_asker(self._ask)

        # Every tool call and result travels as an event. The sink is sync and never
        # raises: a broken client must not fail the tool that triggered it.
        composition.tool_logger.add_sink(self._on_tool_event)

        # The tree moves while subagents work; the sidebar follows it.
        composition.on_idle(self._on_tree_idle)
        composition.tree_changed = self._on_tree_change

    # --- state ---------------------------------------------------------------

    @property
    def session(self) -> Any:
        return self.composition.session

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Builds the MCP layer in the background, like the terminal client does.

        It is a background task and not a startup await: a cold `npx` server can take
        seconds, and the page must be able to load and say "starting" in the meantime.
        """
        loader = self.composition.mcp_loader()
        if loader is None:
            return

        async def load() -> None:
            try:
                await asyncio.to_thread(loader)
            except Exception as exc:  # noqa: BLE001 - startup must survive a broken server
                self.hub.publish('notice', text=f'MCP: {exc}', tone='danger')
            self.hub.publish_state('mcp')

        asyncio.get_event_loop().create_task(load())

    # --- a turn --------------------------------------------------------------

    async def send(self, text: str) -> dict[str, Any]:
        """Accepts a message: a new turn when idle, a steer when one is running."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError('The message is empty.')
        if self.busy:
            queued = self._steer(cleaned)
            return {'accepted': 'steer', 'delivered': queued}
        latest = self.composition.store.refresh(self.session)
        if latest is not None:
            self.composition.bind_session(latest)
            self.hub.publish_state('session_reloaded')
        self._task = asyncio.get_event_loop().create_task(self._turn(cleaned))
        return {'accepted': 'turn'}

    def _steer(self, text: str) -> bool:
        run = self._run
        if run is None:
            return False
        try:
            run.enqueue(text)
            return True
        except Exception:  # noqa: BLE001 - a refused steer is reported, not raised
            return False

    def abort(self) -> bool:
        """Stops the turn: the shell commands die, then the run unwinds."""
        if not self.busy:
            return False
        running_commands.kill_all()
        self.composition.summoner.cancel_all()
        self._resolve_question(None)
        run = self._run
        if run is not None:
            try:
                run.cancel()
            except Exception as exc:  # noqa: BLE001 - cancelling a finished run is a no-op
                # A run the graph already closed refuses the cancel; the turn was
                # ending anyway, so this is not a failure to report.
                self.hub.publish('notice', text=f'Abort ignored: {exc}', tone='muted')
        return True

    async def wait(self) -> None:
        """Waits for the current turn, if any. Used by tests and by shutdown."""
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def _checkpoint(self, *, force: bool = False) -> None:
        """Persists progress at most once per second, so a crash keeps the turn."""
        run = self._run
        if run is None:
            return
        messages = run.all_messages()
        if not messages:
            return
        session = self._turn_session or self.session
        current = session.messages
        if len(messages) == len(current) and current and messages[-1] is current[-1]:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint_at < CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint_at = now
        session.messages = list(messages)
        self.composition.store.save_progress(session)

    async def _turn(self, text: str) -> None:
        composition = self.composition
        session = composition.session
        self._turn_session = session
        self._last_checkpoint_at = 0.0
        self.hub.publish('turn_started', session_id=session.id)
        self._name_session(session, text)

        budget = StepBudget(max_steps=self._budget(), role='turn')
        agent = composition.agent
        status = 'ok'
        error = ''

        try:
            history = session.messages
            recovery = recover_history(history)
            if recovery.changed:
                history = recovery.messages
                session.messages = history
                composition.store.save_progress(session)
                self.hub.publish('notice', text=recovery.notice, tone='warn')

            async with agent.iter(
                user_content(text),
                message_history=history,
                capabilities=[ProcessEventStream(handler=self._stream), budget],
                model_settings=model_settings(composition.model_service),
                usage_limits=UsageLimits(request_limit=None),
            ) as run:
                self._run = run
                async for _node in run:
                    self._checkpoint()
            history = run.all_messages()
        except RunCancelled:
            status = 'cancelled'
            if self._run is not None:
                history = self._run.all_messages()
        except Exception as exc:  # noqa: BLE001 - the turn reports, the server survives
            status = 'error'
            error = str(exc)
            if self._run is not None:
                history = self._run.all_messages()
        finally:
            self._run = None
            self._turn_session = None
            self._resolve_question(None)

        history = recover_history(history).messages
        session.messages = history
        current = composition.store.save_current(session)
        if current is not session:
            composition.bind_session(current)
            self.hub.publish(
                'notice',
                text='Session changed elsewhere; local progress was saved in a recovered session.',
                tone='warn',
            )

        if budget.exhausted:
            self.hub.publish(
                'notice',
                text=f'Step limit of {budget.max_steps} reached: the turn got one last call without tools.',
                tone='warn',
            )
        self.hub.publish('turn_finished', status=status, error=error, session_id=session.id)
        self.hub.publish_state('turn')

    def _budget(self) -> int:
        """The profile's own budget, or the configured default — the same rule as the TUI."""
        profile = self.composition.agent_registry.active
        declared = getattr(profile, 'max_steps', None)
        return declared if declared else self.composition.settings.max_steps

    def _name_session(self, session: Any, text: str) -> None:
        """Titles the conversation after its first message, off the turn's path.

        It costs a model call, so it runs as its own task: the turn must not wait for
        a title, and a title that fails is not a failed conversation.
        """
        if session.title:
            return

        async def name() -> None:
            try:
                from src.engine.sessions.titling import generate_title

                title = await generate_title(self.composition.model_service.current_model(), text)
            except Exception:  # noqa: BLE001 - a title is a nicety
                return
            if not title:
                return
            session.title = title
            self.composition.store.save_progress(session)
            self.hub.publish_state('session_named')

        asyncio.get_event_loop().create_task(name())

    # --- the stream ----------------------------------------------------------

    async def _stream(self, _ctx: Any, events: Any) -> None:
        """Turns a pydantic-ai stream into events a browser can apply."""
        async for event in events:
            self._checkpoint()
            if isinstance(event, PartStartEvent):
                part = event.part
                if isinstance(part, ThinkingPart) and part.content:
                    self.hub.publish('thinking_delta', text=part.content)
                elif isinstance(part, TextPart) and part.content:
                    self.hub.publish('text_delta', text=part.content)
            elif isinstance(event, PartDeltaEvent):
                delta = event.delta
                if isinstance(delta, TextPartDelta) and delta.content_delta:
                    # The one event that must not be batched: it is the typing.
                    self.hub.publish('text_delta', text=delta.content_delta)
                elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                    self.hub.publish('thinking_delta', text=delta.content_delta)
            elif isinstance(event, FunctionToolResultEvent):
                ok = getattr(event.part, 'outcome', 'success') == 'success'
                content = present_output(event.part.tool_name, event.part.content).text
                self.hub.publish(
                    'tool_result',
                    tool_name=event.part.tool_name,
                    call_id=str(getattr(event.part, 'tool_call_id', '')),
                    ok=ok,
                    preview=content[:PREVIEW_CHARS],
                )
            elif isinstance(event, EnqueuedMessagesEvent):
                self.hub.publish('steer_delivered', enqueue_id=str(event.enqueue_id))

    def _on_tool_event(self, event: Any) -> None:
        """The tool log sink: what started, how long it took, how it ended."""
        if getattr(event, 'node_id', None) is not None:
            return
        phase = getattr(event, 'phase', '')
        if phase == 'start':
            self.hub.publish(
                'tool_call',
                tool_name=event.tool_name,
                args=_jsonable(event.args),
                invocation_id=getattr(event, 'invocation_id', ''),
            )
            return
        summary = getattr(event, 'summary', None)
        self.hub.publish(
            'tool_finished',
            tool_name=event.tool_name,
            phase=phase,
            invocation_id=getattr(event, 'invocation_id', ''),
            ok=phase != 'error',
            error=event.error,
            duration_ms=round(event.duration_ms or 0.0, 1),
            preview=(event.output if getattr(event, 'output', None) is not None else '')[:PREVIEW_CHARS],
            summary=None if summary is None else {'text': summary.text, 'status': summary.status},
        )

    # --- questions -----------------------------------------------------------

    async def _ask(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        """Publishes the questions and waits for the browser to answer them."""
        self._question_ids += 1
        pending = PendingQuestion(id=f'q{self._question_ids}', questions=questions)
        self._pending_question = pending
        self.hub.publish(
            'question',
            id=pending.id,
            questions=[
                {
                    'question': item.question,
                    'allow_custom': item.allow_custom,
                    'options': [
                        {'label': option.label, 'description': option.description, 'recommended': option.recommended}
                        for option in item.options
                    ],
                }
                for item in questions
            ],
        )
        try:
            return await pending.future
        except asyncio.CancelledError:
            self._pending_question = None
            raise

    def answer(self, question_id: str, answers: list[dict[str, Any]] | None) -> bool:
        """Resolves the pending question. ``None`` means the user cancelled."""
        pending = self._pending_question
        if pending is None or pending.id != question_id:
            return False
        parsed: list[QuestionAnswer] | None = None
        if answers is not None:
            parsed = [
                QuestionAnswer(
                    question=str(item.get('question', '')),
                    answer=str(item.get('answer', '')),
                    custom=bool(item.get('custom', False)),
                )
                for item in answers
            ]
        self._pending_question = None
        if not pending.future.done():
            pending.future.set_result(parsed)
        self.hub.publish('question_closed', id=question_id)
        return True

    def _resolve_question(self, answers: list[QuestionAnswer] | None) -> None:
        """Releases a waiting question with nothing: the turn is not going to use it."""
        pending = self._pending_question
        if pending is None:
            return
        self._pending_question = None
        if not pending.future.done():
            pending.future.set_result(answers)
        self.hub.publish('question_closed', id=pending.id)

    @property
    def pending_question(self) -> PendingQuestion | None:
        return self._pending_question

    # --- tree ----------------------------------------------------------------

    def _on_tree_change(self, node: Any) -> None:
        self.hub.publish('tree_changed', node_id=getattr(node, 'id', ''))

    def _on_tree_idle(self) -> None:
        self.hub.publish_state('tree_idle')

    # --- workspace -----------------------------------------------------------

    def switch_workspace(self, root: str) -> dict[str, Any]:
        """Moves the server to another workspace and opens its last session.

        Refused mid-turn: `os.chdir` while a tool is running would make the tool read
        a different tree than the one it started in.
        """
        if self.busy:
            raise RuntimeError('Cannot switch workspace while a turn is running.')
        switch = self.composition.workspace_manager.switch(root)
        self.composition.root = switch.workspace.root
        store = self.composition.store
        session = store.load_last() or store.create_session()
        self.composition.bind_session(session)
        self.composition.rebuild_agent()
        self.hub.publish_state('workspace')
        return {'root': str(switch.workspace.root), 'label': switch.workspace.label, 'changed': switch.changed}

    def new_session(self) -> Any:
        """Starts an empty conversation in the current workspace."""
        if self.busy:
            raise RuntimeError('Cannot start a session while a turn is running.')
        session = self.composition.store.create_session()
        self.composition.bind_session(session)
        self.hub.publish_state('session')
        return session

    def open_session(self, session_id: str) -> Any:
        """Opens an existing conversation, wherever its workspace is."""
        if self.busy:
            raise RuntimeError('Cannot switch session while a turn is running.')
        target = self.composition.store.load(session_id)
        if target is None:
            raise LookupError(f'No session {session_id}.')
        if target.cwd != self.composition.store.cwd:
            self.composition.workspace_manager.switch(target.cwd)
            self.composition.root = target.cwd
        self.composition.bind_session(target)
        self.hub.publish_state('session')
        return target

    def close(self) -> None:
        self.composition.question_hub.set_asker(None)
        self.composition.tool_logger.remove_sink(self._on_tool_event)
        self.composition.close()


def _jsonable(value: Any) -> Any:
    """Tool arguments as JSON, with bytes summarized instead of base64'd."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f'<{len(value)} bytes>'
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)[:PREVIEW_CHARS]


__all__ = ['CHECKPOINT_INTERVAL', 'PREVIEW_CHARS', 'PendingQuestion', 'ServerRuntime']
