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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.capabilities import ProcessEventStream
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import (
    BinaryContent,
    EnqueuedMessagesEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from src.engine.agents.step_budget import StepBudget
from src.engine.agents.turn_map import library_run_id
from src.engine.attachments.content import stored_content, stored_parts, user_content
from src.engine.bootstrap import Composition, model_settings
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.providers.domain.provider_error import ProviderError
from src.engine.sessions.recovery import recover_history
from src.engine.storage.accepted_inputs import AcceptedInput, AcceptedInputs, DeliveryConfirmation
from src.engine.tooling.outputs import present_output
from src.server.events import EventHub
from src.server.ownership import OwnershipError, ServerOwnership

CHECKPOINT_INTERVAL = 1.0
"""Minimum seconds between checkpoints during a turn. The final save is mandatory."""

QUEUE_POLL_INTERVAL = 0.25
"""Bounded wait for an input accepted outside this runtime. Not a busy loop."""

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

    def __init__(self, composition: Composition, hub: EventHub, *, require_ownership: bool = True) -> None:
        self.composition = composition
        self.hub = hub
        self._require_ownership = require_ownership
        composition.extension_notice = lambda text: hub.publish('notice', text=text, tone='danger')
        self._task: asyncio.Task[None] | None = None
        self._run: Any = None
        self._pending_question: PendingQuestion | None = None
        self._question_ids = 0
        self._last_checkpoint_at = 0.0
        self._turn_session: Any = None

        # Durable consumer. The queue is the source of truth for accepted work;
        # the in-memory maps only connect a request to the enqueue it produced.
        self._accepted = AcceptedInputs(composition.store.database)
        self._ownership = ServerOwnership(composition.store.database.path)
        self._started = False
        self._closing = False
        self._consumer: asyncio.Task[None] | None = None
        self._consumer_error: BaseException | None = None
        self._wake = asyncio.Event()
        self._settled = asyncio.Event()
        self._settled.set()
        self._aborting = False
        self._attempts: dict[str, str] = {}
        self._pending: dict[str, AcceptedInput] = {}
        self._enqueued: dict[str, str] = {}
        self._event_seen: set[str] = set()
        self._confirmed: set[str] = set()
        self._finished: set[str] = set()
        self._mcp_started = False

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
        """Owns the database before touching it, then reconciles the queue.

        Reconciliation is a state change, not a replay: every input that was not
        terminal becomes ``interrupted``. The lock is held until ``close``.
        """
        if self._started:
            return
        self._ownership.acquire()
        self._accepted.interrupt_open()
        self._started = True

    def ensure_workers(self) -> None:
        """Starts the durable consumer and the MCP loader on the running loop.

        The consumer requires the database lock. Only tests that exercise the
        runtime without a server opt out through ``require_ownership=False``.
        """
        if self._require_ownership and not self._ownership.held:
            raise OwnershipError('The queue consumer requires the database lock')
        if self._consumer is None:
            self._consumer = asyncio.get_running_loop().create_task(self._consume())
        self._start_mcp()

    def _start_mcp(self) -> None:
        if self._mcp_started:
            return
        loader = self.composition.mcp_loader()
        if loader is None:
            return
        self._mcp_started = True

        async def load() -> None:
            try:
                await asyncio.to_thread(loader)
            except Exception as exc:  # noqa: BLE001 - startup must survive a broken server
                self.hub.publish('notice', text=f'MCP: {exc}', tone='danger')
            self.hub.publish_state('mcp')

        asyncio.get_running_loop().create_task(load())

    # --- a turn --------------------------------------------------------------

    async def send(self, text: str, request_id: str | None = None) -> dict[str, Any]:
        """Accepts a message durably; the consumer turns it or steers it.

        ``accepted`` keeps its meaning, ``request_id`` is the durable identity, and
        a steer also reports ``delivered`` for the existing web client. That flag
        means the steer was accepted durably as a steer, not that it entered the
        run yet; the run confirms it later inside a checkpoint. The fields are
        additive; the old response remains readable.
        """
        cleaned = text.strip()
        if not cleaned:
            raise ValueError('The message is empty.')
        if request_id == '':
            raise ValueError('request_id must not be empty')
        self.ensure_workers()
        if self._consumer_error is not None or (self._consumer is not None and self._consumer.done()):
            raise RuntimeError(f'Queue consumer stopped: {self._consumer_error or "closed"}')
        latest = self.composition.store.refresh(self.session)
        if latest is not None:
            self.composition.bind_session(latest)
            self.hub.publish_state('session_reloaded')
        kind = 'steer' if self.busy else 'turn'
        identifier = request_id or str(uuid.uuid4())
        existing = self.composition.store.database.one(
            'SELECT kind, state FROM accepted_inputs WHERE session_id = ? AND request_id = ?',
            (self.session.id, identifier),
        )
        accepted = self.composition.store.accept_input(self.session, identifier, cleaned, [], kind)
        self._settled.clear()
        self._wake.set()
        response = {
            'accepted': kind if existing is None else str(existing['kind']),
            'request_id': accepted.request_id,
            'duplicate': existing is not None,
        }
        if existing is not None:
            response['state'] = str(existing['state'])
        elif kind == 'steer':
            response['delivered'] = True
        return response

    async def _consume(self) -> None:
        """Claims and runs accepted inputs, oldest first, until closed."""
        while not self._closing:
            try:
                item = self._claim_next()
                if item is None:
                    self._settled.set()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=QUEUE_POLL_INTERVAL)
                    except TimeoutError:
                        pass
                    self._wake.clear()
                    continue
                self._settled.clear()
                if item.kind == 'steer' and self._run is None:
                    # The turn ended before the steer reached its seam. A steer is
                    # never promoted to a later turn: leave it visibly stopped.
                    self._accepted.transition(item.session_id, item.request_id, 'stopped')
                    self._pending.pop(item.request_id, None)
                    self._attempts.pop(item.request_id, None)
                    continue
                if item.kind == 'steer':
                    self._enqueue(item)
                    continue
                self._task = asyncio.get_running_loop().create_task(self._turn(item.text, item))
                await self._task
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the consumer reports and stops
                self._consumer_error = exc
                # Nobody is left to consume accepted rows: make them visible as
                # interrupted instead of leaving them hanging in ``accepted``.
                self._accepted.interrupt_open()
                self.hub.publish('notice', text=f'Queue consumer: {exc}', tone='danger')
                self._settled.set()
                return
        self._settled.set()

    def _claim_next(self) -> AcceptedInput | None:
        item = self._accepted.claim_next(self.session.id, attempt_id=uuid.uuid4().hex)
        if item is None:
            return None
        self._attempts[item.request_id] = item.attempt_id or ''
        self._pending[item.request_id] = item
        return item

    def _enqueue(self, item: AcceptedInput) -> None:
        run = self._run
        if run is None:
            self._accepted.transition(item.session_id, item.request_id, 'stopped')
            self._pending.pop(item.request_id, None)
            return
        try:
            enqueue_id = run.enqueue(*stored_parts(item.text, item.images))
        except Exception:  # noqa: BLE001 - a refused steer is visible, not fatal
            self._accepted.transition(item.session_id, item.request_id, 'stopped')
            self._pending.pop(item.request_id, None)
            return
        self._enqueued[item.request_id] = str(enqueue_id)

    def _pump_steering(self) -> None:
        """Claims everything already accepted and hands it to the active run."""
        if self._aborting:
            return
        while self._run is not None:
            item = self._claim_next()
            if item is None:
                return
            self._enqueue(item)

    def abort(self) -> bool:
        """Stops the turn: the shell commands die, then the run unwinds."""
        if not self.busy:
            return False
        self._aborting = True
        self.composition.shell_commands.kill_all()
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
        self._stop_pending(self.session.id, keep=frozenset(self._enqueued))
        return True

    def _stop_pending(self, session_id: str, *, keep: frozenset[str] = frozenset()) -> None:
        """Finishes confirmed deliveries, stops the rest, then forgets both.

        ``keep`` requests are already in the live run; abort must not stop them
        before that run has had the chance to deliver or discard them.
        """
        confirmed = [
            request_id
            for request_id in self._confirmed
            if (item := self._pending.get(request_id)) is not None and item.session_id == session_id
        ]
        for request_id in confirmed:
            item = self._pending[request_id]
            try:
                self._accepted.transition(item.session_id, request_id, 'finished')
            except ValueError:
                pass
            self._finished.add(request_id)
            self._pending.pop(request_id, None)
            self._attempts.pop(request_id, None)
            self._enqueued.pop(request_id, None)
            self._event_seen.discard(request_id)
            self._confirmed.discard(request_id)
        self._accepted.stop_open(session_id, keep=keep)
        for request_id, item in list(self._pending.items()):
            if item.session_id != session_id:
                continue
            self._pending.pop(request_id, None)
            self._attempts.pop(request_id, None)
            self._enqueued.pop(request_id, None)
            self._event_seen.discard(request_id)
            self._confirmed.discard(request_id)

    async def wait(self) -> None:
        """Waits until the consumer has no accepted or running work."""
        if self._consumer is not None:
            await self._settled.wait()
            if self._consumer_error is not None:
                raise self._consumer_error

    def _checkpoint(self, *, force: bool = False) -> None:
        """Persists progress, and delivery facts only when forced by a delivery event."""
        run = self._run
        if run is None:
            return
        messages = run.all_messages()
        if not messages:
            return
        session = self._turn_session or self.session
        current = session.messages
        unchanged = len(messages) == len(current) and bool(current) and messages[-1] is current[-1]
        confirmations = self._confirmations(run) if force else []
        if unchanged and not confirmations:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint_at < CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint_at = now
        session.messages = list(messages)
        try:
            result = self.composition.store.save_progress_result(session, confirmations=confirmations)
        except Exception as exc:  # noqa: BLE001 - a failed checkpoint is reported, not fatal
            self.hub.publish('notice', text=f'Checkpoint: {exc}', tone='warn')
            return
        self._confirmed.update(confirmation.request_id for confirmation in confirmations)
        if result is not None and result.recovery_session_id:
            recovered = self.composition.store.load(result.recovery_session_id)
            if recovered is not None:
                self.composition.bind_session(recovered)
                self._turn_session = recovered
                self.hub.publish(
                    'notice',
                    text='Session changed elsewhere; local progress was saved in a recovered session.',
                    tone='warn',
                )

    async def _turn(self, text: str, accepted: AcceptedInput | None = None) -> None:
        composition = self.composition
        session = composition.session
        self._turn_session = session
        self._last_checkpoint_at = 0.0
        self._aborting = False
        if accepted is not None:
            # The turn's own prompt is the delivery: it is passed to agent.iter, so
            # its presence in the snapshot is the proof, just like a steer's event.
            self._event_seen.add(accepted.request_id)
        self.hub.publish('turn_started', session_id=session.id)
        self._name_session(session, text)

        budget = StepBudget(max_steps=self._budget(), role='turn')
        agent = composition.agent
        status = 'ok'
        error = ''
        run_for_final: Any = None
        prompt = stored_content(text, accepted.images) if accepted is not None else user_content(text)

        try:
            history = session.messages
            recovery = recover_history(history)
            if recovery.changed:
                history = recovery.messages
                session.messages = history
                composition.store.save_progress(session)
                self.hub.publish('notice', text=recovery.notice, tone='warn')

            async with agent.iter(
                prompt,
                message_history=history,
                capabilities=[ProcessEventStream(handler=self._stream), budget],
                model_settings=model_settings(composition.model_service),
                usage_limits=UsageLimits(request_limit=None),
            ) as run:
                self._run = run
                self._pump_steering()
                async for _node in run:
                    self._pump_steering()
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
            run_for_final = self._run
            self._run = None
            self._turn_session = None
            self._resolve_question(None)

        history = recover_history(history).messages
        session.messages = history
        confirmations = self._confirmations(run_for_final)
        try:
            composition.store.save(session, confirmations=confirmations)
        except Exception as exc:  # a conflicting winner keeps the turn alive
            from src.engine.sessions.session_store import SessionConflictError

            if not isinstance(exc, SessionConflictError):
                raise
            composition.store.save_progress_result(session, confirmations=confirmations)
            latest = composition.store.load(session.id)
            if latest is not None:
                composition.bind_session(latest)
                self.hub.publish(
                    'notice',
                    text='Session changed elsewhere; local progress was saved in a recovered session.',
                    tone='warn',
                )
        self._confirmed.update(confirmation.request_id for confirmation in confirmations)
        self._finish_confirmed(set(self._confirmed))
        if status == 'cancelled' or self._aborting:
            self._stop_pending(session.id)
            self._aborting = False

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
            self._pump_steering()
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
                request_id = self._request_for_enqueue(str(event.enqueue_id))
                if request_id is not None:
                    self._event_seen.add(request_id)
                    # Only a snapshot forced by the delivery event may confirm it.
                    self._checkpoint(force=True)
                self.hub.publish('steer_delivered', enqueue_id=str(event.enqueue_id))

    def _request_for_enqueue(self, enqueue_id: str) -> str | None:
        for request_id, value in self._enqueued.items():
            if value == enqueue_id:
                return request_id
        return None

    def _confirmations(self, run: Any) -> list[DeliveryConfirmation]:
        """The deliveries this snapshot proves, one confirmation per request."""
        if run is None:
            return []
        messages = run.all_messages()
        confirmations: list[DeliveryConfirmation] = []
        for request_id in self._event_seen:
            if request_id in self._confirmed:
                continue
            item = self._pending.get(request_id)
            attempt = self._attempts.get(request_id)
            if item is None or not attempt:
                continue
            message = _matching_message(messages, item)
            if message is None:
                continue
            turn_identifier = library_run_id(message)
            if not turn_identifier:
                continue
            confirmations.append(DeliveryConfirmation(item.session_id, request_id, attempt, turn_identifier))
        return confirmations

    def _finish_confirmed(self, request_ids: set[str]) -> None:
        """Moves confirmed deliveries to finished after the final save committed."""
        for request_id in request_ids:
            item = self._pending.get(request_id)
            if item is None:
                continue
            try:
                self._accepted.transition(item.session_id, request_id, 'finished')
            except ValueError:
                continue
            self._finished.add(request_id)
            self._pending.pop(request_id, None)
            self._attempts.pop(request_id, None)
            self._enqueued.pop(request_id, None)
            self._event_seen.discard(request_id)
            self._confirmed.discard(request_id)

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
        session = store.load_last() or store.draft_session()
        self.composition.bind_session(session)
        self.composition.rebuild_agent()
        self.hub.publish_state('workspace')
        return {'root': str(switch.workspace.root), 'label': switch.workspace.label, 'changed': switch.changed}

    def new_session(self) -> Any:
        """Starts an empty conversation in the current workspace."""
        if self.busy:
            raise RuntimeError('Cannot start a session while a turn is running.')
        session = self.composition.store.draft_session()
        session.agent = self.composition.agent_registry.active_name
        try:
            selected = self.composition.select_agent_model(session.agent)
        except ProviderError as exc:
            raise RuntimeError(f'Agent model: {exc}') from exc
        session.selection = (selected or self.composition.model_service.snapshot()).selection
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
        self._closing = True
        if self._consumer is not None and not self._consumer.done():
            try:
                self._consumer.cancel()
            except RuntimeError:  # pragma: no cover - the loop already stopped
                pass
        if self._started:
            try:
                self._accepted.interrupt_open()
            finally:
                self._ownership.release()
                self._started = False
        self.composition.question_hub.set_asker(None)
        self.composition.tool_logger.remove_sink(self._on_tool_event)
        self.composition.close()


def _prompt_matches(part: UserPromptPart, item: AcceptedInput) -> bool:
    """Whether a history prompt carries exactly the accepted text and images."""
    content = part.content
    if not item.images:
        return content == item.text
    if isinstance(content, str):
        return False
    values = list(content)
    if not values or values[0] != item.text:
        return False
    images = [value for value in values[1:] if isinstance(value, BinaryContent)]
    if len(images) != len(item.images):
        return False
    return all(
        image.data == stored.data and image.media_type == stored.media_type
        for image, stored in zip(images, item.images, strict=True)
    )


def _matching_message(messages: Sequence[ModelMessage], item: AcceptedInput) -> ModelMessage | None:
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart) and _prompt_matches(part, item):
                return message
    return None


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
