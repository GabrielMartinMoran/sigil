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

`SessionRuntime` is also the ownership boundary. A turn, the queue it claims, the
question it waits on, the steering it hands over and the reports it wakes for are
session state; the database lock, the store, the hub and the idle decision are
process state and live on `src.server.server_runtime.ServerRuntime`, which passes what the
session needs. This module therefore never imports the facade.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal

from pydantic_ai.capabilities import ProcessEventStream
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import (
    EnqueuedMessagesEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from src.core.agents.agent_model_config import AgentModelConfig
from src.core.agents.checkpoint import Checkpoint
from src.core.agents.config_scope import ConfigScope
from src.core.agents.fork_outcome import ForkOutcome
from src.core.agents.mark import mark
from src.core.agents.message_types import MessageTypes
from src.core.agents.model_defaults_state import ModelDefaultsState
from src.core.agents.origin import origin
from src.core.agents.profile.models.agent_profile import AgentProfile
from src.core.agents.retry_notice import RetryNotice
from src.core.agents.rollback_outcome import RollbackOutcome
from src.core.agents.step_budget import StepBudget
from src.core.agents.tree.models.agent_node import AgentNode
from src.core.agents.tree.models.node_status import NodeStatus
from src.core.agents.tree.models.summon_error import SummonError
from src.core.agents.tree.reports import WAKE_PROMPT, report_view
from src.core.agents.turn_map import library_run_id
from src.core.agents.turn_timings import TurnTimings
from src.core.attachments.content import stored_content, stored_parts, user_content
from src.core.composition.composition import Composition
from src.core.composition.model_settings import model_settings
from src.core.context.capability import subagent_window_warning
from src.core.context.compaction.manual_compaction_result import ManualCompactionResult
from src.core.files.paths import execution_workspace
from src.core.goal.command import GoalUpdate, apply_goal_command
from src.core.goal.coordinator import ProgressTracker
from src.core.goal.policy import GoalQuestionGate
from src.core.goal.review import GoalReviewInput, conversation_context, review_goal
from src.core.goal.state import GoalCommand, goal_instructions
from src.core.interaction.models.question_answer import QuestionAnswer
from src.core.interaction.models.question_item import QuestionItem
from src.core.sessions.recover_history import recover_history
from src.core.storage.accepted_input import AcceptedInput
from src.core.storage.accepted_inputs import AcceptedInputs
from src.core.storage.delivery_confirmation import DeliveryConfirmation
from src.core.tooling.outputs import present_output
from src.server.runtime.event_hub import EventHub
from src.server.runtime.incoming_image import IncomingImage
from src.server.runtime.pending_question import PendingQuestion
from src.server.runtime.runtime_support import (
    CHECKPOINT_INTERVAL,
    PREVIEW_CHARS,
    QUEUE_POLL_INTERVAL,
    SLOW_TURN_NOTICE_SECONDS,
    OwnershipGuard,
    decode_images,
    jsonable,
    matching_message,
)


def _not_busy() -> bool:
    """The model service's own default: nothing this session owns is running.

    A closed session leaves this in place of its own flag, so a model swap is
    refused only by a session that is still alive to have one.
    """
    return False


class SessionRuntime:
    """The execution state and callbacks of one session.

    What a turn is doing belongs here: the run, the rows it claims, the question it
    waits on, the steering it hands to `run.enqueue`, the wake a finished subagent
    owes and the callbacks the engine calls. What the process owns stays on the
    facade: the database lock, the accepted-inputs handle it passes in, the event
    hub it publishes on and the idle decision.

    This object is created once and exists so the ownership is explicit before
    concurrency is added. It does not make the store concurrent: changing the
    session still rebinds the composition in place, which is the behaviour this
    refactor preserves.
    """

    def __init__(
        self,
        composition: Composition,
        hub: EventHub,
        accepted: AcceptedInputs,
        *,
        question_timeout: float | None,
        require_worker_ownership: OwnershipGuard,
    ) -> None:
        self.composition = composition
        self.hub = hub
        # The queue is shared with the facade, which reconciles it on `start` and
        # answers the service-wide idle question from it.
        self._accepted = accepted
        # The lock is the facade's; this only asks whether it is still held.
        self._require_worker_ownership = require_worker_ownership
        # Resolved once, by the facade: a question asked while the service runs
        # cannot change how long it is allowed to wait.
        self._question_timeout = question_timeout
        self._task: asyncio.Task[None] | None = None
        self._compacting = False
        self._compaction_done = asyncio.Event()
        self._compaction_done.set()
        self._run: Any = None
        self._turn_timings: TurnTimings | None = None
        self._timing_session_id: str | None = None
        self._pending_question: PendingQuestion | None = None
        self._question_ids = 0
        self._last_checkpoint_at = 0.0
        self._turn_session: Any = None
        self._turn_activity: asyncio.Event | None = None

        # Durable consumer. The in-memory maps only connect a request to the
        # enqueue it produced; the queue is the source of truth for accepted work.
        self._closing = False
        self._consumer: asyncio.Task[None] | None = None
        self._consumer_error: BaseException | None = None
        self._wake = asyncio.Event()
        self._settled = asyncio.Event()
        self._settled.set()
        self._aborting = False
        self._goal_needs_review = False
        self._goal_review_task: asyncio.Task[Any] | None = None
        self._goal_progress = ProgressTracker()
        self._goal_progress_key: tuple[str, int] | None = None
        self._goal_enqueues: set[str] = set()
        self._attempts: dict[str, str] = {}
        self._pending: dict[str, AcceptedInput] = {}
        self._enqueued: dict[str, str] = {}
        self._event_seen: set[str] = set()
        self._confirmed: set[str] = set()
        self._finished: set[str] = set()
        self._mcp_started = False
        self._mcp_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._title_tasks: set[asyncio.Task[None]] = set()

        composition.extension_notice = lambda text: self._publish('notice', text=text, tone='danger')
        composition.context_capability.on_unknown_window = lambda node, model: self._publish(
            'notice', text=subagent_window_warning(node, model), tone='warn'
        )

        # The agent asks through the hub; the browser answers over HTTP.
        composition.question_hub.set_asker(self._ask)

        # "Busy" is this session's fact, and the model service is the object that
        # refuses a swap while it holds. Pointing the service at this flag is what
        # makes the HTTP command get the engine's own `BusyError` instead of a
        # second rule written in a route — the same refusal the terminal gets.
        composition.model_service.is_busy = lambda: self.busy

        # Every tool call and result travels as an event. The sink is sync and never
        # raises: a broken client must not fail the tool that triggered it.
        composition.tool_logger.add_sink(self._on_tool_event)

        # A turn whose model is retrying is alive, and until this existed it looked
        # dead: five attempts with a backoff of up to eight seconds published nothing
        # because nothing subscribed. The terminal paints the same notice in-process
        # (`SigilApp.notify_model_retry`); a service has no chat of its own, so the
        # notice travels on the hub for every attached client.
        composition.model_retry.on_retry = self._on_model_retry

        # The tree moves while subagents work; the sidebar follows it.
        composition.on_idle(self._on_tree_idle)
        composition.tree_changed = self._on_tree_change

    def _publish(self, event_type: str, **data: Any) -> None:
        self.hub.publish(event_type, owner_session_id=self.session.id, **data)

    def _publish_state(self, reason: str) -> None:
        self.hub.publish_state(reason, owner_session_id=self.session.id)

    # --- state ---------------------------------------------------------------

    @property
    def session(self) -> Any:
        return self.composition.session

    @property
    def busy(self) -> bool:
        return (
            self._compacting
            or (self._task is not None and not self._task.done())
            or (self._goal_review_task is not None and not self._goal_review_task.done())
        )

    def ensure_workers(self) -> None:
        """Starts the durable consumer and the MCP loader on the running loop.

        The consumer requires the database lock, and this object does not hold it,
        so the guard is asked first: it is the facade's callable, and it raises
        `OwnershipError` when the process does not own the store. Only tests that
        exercise the runtime without a server opt out through
        ``require_ownership=False``.
        """
        self._require_worker_ownership()
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
            gateway = self.composition.mcp
            try:
                manager = await asyncio.to_thread(loader)
            except Exception as exc:  # noqa: BLE001 - startup must survive a broken server
                # `fail` releases whoever is waiting on the layer. Without it the
                # ready event is never set and `GET /api/mcp` waits forever on a
                # manager that is not coming.
                gateway.fail(str(exc))
                self._publish('notice', text=f'MCP: {exc}', tone='danger')
            else:
                try:
                    gateway.install(manager)
                except Exception:
                    await manager.aclose()
                    raise
            self._publish_state('mcp')

        self._mcp_task = asyncio.get_running_loop().create_task(load())

    # --- a turn --------------------------------------------------------------

    async def send(
        self, text: str, request_id: str | None = None, images: Sequence[IncomingImage] = ()
    ) -> dict[str, Any]:
        """Accept user input without allowing clients to assign internal types."""
        return await self._accept_message(text, request_id, images)

    async def _accept_message(
        self,
        text: str,
        request_id: str | None = None,
        images: Sequence[IncomingImage] = (),
        *,
        message_origin: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Accepts a message durably; the consumer turns it or steers it.

        ``accepted`` keeps its meaning, ``request_id`` is the durable identity, and
        a steer also reports ``delivered`` for the existing web client. That flag
        means the steer was accepted durably as a steer, not that it entered the
        run yet; the run confirms it later inside a checkpoint. The fields are
        additive; the old response remains readable.

        ``images`` are the pictures of the message, in the order the text marks
        them. They are validated and re-encoded before the queue is touched, so a
        refusal leaves nothing behind; ``images`` in the response carries the
        identifiers that were persisted, which a retry gets back unchanged.
        """
        if self._compacting:
            raise ValueError('Context compaction is in progress; try again when it finishes.')
        cleaned = text.strip()
        if not cleaned:
            raise ValueError('The message is empty.')
        if request_id == '':
            raise ValueError('request_id must not be empty')
        payload = decode_images(images)
        self.ensure_workers()
        if self._consumer_error is not None or (self._consumer is not None and self._consumer.done()):
            raise RuntimeError(f'Queue consumer stopped: {self._consumer_error or "closed"}')
        latest = self.composition.store.refresh(self.session)
        if latest is not None:
            self.composition.bind_session(latest)
            self._publish_state('session_reloaded')
        kind = 'steer' if self._task is not None and not self._task.done() else 'turn'
        identifier = request_id or str(uuid.uuid4())
        existing = self.composition.store.database.one(
            'SELECT kind, state FROM accepted_inputs WHERE session_id = ? AND request_id = ?',
            (self.session.id, identifier),
        )
        accepted = self.composition.store.accept_input(
            self.session, identifier, cleaned, payload, kind, origin=message_origin
        )
        self._settled.clear()
        self._wake.set()
        if existing is None:
            # Other clients have to learn that a message entered the queue. The event
            # carries the fact and not the text: the rows are the text, and a copy in
            # the event would be a second version of it to keep in step — the screen
            # reads the rows again instead. A duplicate retry wrote no row, so it
            # announces nothing.
            self._publish_state('queue')
        response = {
            'accepted': kind if existing is None else str(existing['kind']),
            'request_id': accepted.request_id,
            'duplicate': existing is not None,
            'images': [{'id': image.image_id, 'media_type': image.media_type} for image in accepted.images],
        }
        if existing is not None:
            response['state'] = str(existing['state'])
        elif kind == 'steer':
            response['delivered'] = True
        return response

    def _subagent_working(self) -> bool:
        """Whether any subagent of the session is still in a run.

        The root is not one: it is the agent of the conversation, and its turn is
        bookkeeping this loop already owns.
        """
        tree = self.composition.summoner.tree()
        return any(node.status is NodeStatus.RUNNING for node in tree.nodes() if not node.is_root)

    async def _wake_from_subagents(self) -> bool:
        """Starts the turn the finished subagents are owed, once, and reports it.

        The terminal debounces `subagents_idle` with a timer; here the fact that
        decides is coarser and needs no clock: **a report is read when the squad is
        quiet**. Three researchers that finish within a second are three answers and
        one turn, because the question is whether any subagent is still working, not
        how long ago the last one stopped.

        The reports become a durable input like any other, so the woken turn is
        claimed, checkpointed and confirmed by the same code a typed message is —
        and a service that dies before running it leaves an `interrupted` row rather
        than a lost message that something would re-execute later.

        Returns:
            Whether a turn was started, so the consumer loops instead of parking.
        """
        if self._closing or self.busy:
            return False
        if self._subagent_working():
            return False
        nodes = [node for node in self.composition.summoner.waiting() if str(node.status) in ('done', 'failed')]
        snapshots = [report_view(node) for node in nodes]
        reports = self.composition.summoner.take_reports()
        if not reports:
            return False
        await self._accept_message(
            '\n\n'.join([*reports, WAKE_PROMPT]), message_origin=origin('subagent_report', reports=snapshots)
        )
        return True

    async def _consume(self) -> None:
        """Claims and runs accepted inputs, oldest first, until closed."""
        while not self._closing:
            try:
                if self._compacting:
                    await self._compaction_done.wait()
                    continue
                item = self._claim_next()
                if item is None:
                    # Nothing was typed, but a subagent may have answered: the
                    # wake is a turn, and this is the one place that starts them.
                    if await self._wake_from_subagents():
                        continue
                    if self._goal_needs_review and not self._subagent_working():
                        if await self._review_goal():
                            continue
                        if self._has_open_input():
                            continue
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
                self._accepted.interrupt_open(self.session.id)
                self._publish('notice', text=f'Queue consumer: {exc}', tone='danger')
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
        if self._task is None or self._task.done():
            review = self._goal_review_task
            if (review is None or review.done()) and not self._goal_needs_review:
                return False
            goal = self.session.goal
            if goal is not None:
                self.session.goal = replace(goal, status='stopped', revision=goal.revision + 1)
                self.composition.store.save_progress(self.session)
            self._goal_needs_review = False
            if review is not None and not review.done():
                review.cancel()
            self.composition.summoner.cancel_all()
            self._publish('turn_finished', status='cancelled', error='', session_id=self.session.id)
            self._publish_state('goal')
            return True
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
                self._publish('notice', text=f'Abort ignored: {exc}', tone='muted')
        self._stop_pending(self.session.id, keep=frozenset(self._enqueued))
        return True

    def stop_all(self) -> bool:
        """Stops the turn and everything else the session still runs.

        `abort` is scoped to a turn: with nothing running it answers ``False``, and
        a subagent working in the background is not a turn — it would keep spending
        after the screen it reported to was gone. Leaving the terminal means the
        whole session, so it asks for this. Nothing here kills the process.
        """
        stopped = self.abort()
        self.composition.summoner.cancel_all()
        return stopped

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
            self._publish('notice', text=f'Checkpoint: {exc}', tone='warn')
            return
        self._confirmed.update(confirmation.request_id for confirmation in confirmations)
        if result is not None and result.recovery_session_id:
            recovered = self.composition.store.load(result.recovery_session_id)
            if recovered is not None:
                self.composition.bind_session(recovered)
                self._turn_session = recovered
                self._publish(
                    'notice',
                    text='Session changed elsewhere; local progress was saved in a recovered session.',
                    tone='warn',
                )

    async def _turn(self, text: str, accepted: AcceptedInput | None = None) -> None:
        composition = self.composition
        session = composition.session
        goal = session.goal
        if (
            goal is not None
            and goal.status in ('waiting', 'blocked')
            and accepted is not None
            and (accepted.origin or {}).get('type', 'user') == 'user'
        ):
            session.goal = replace(goal, status='armed', continuations=0, revision=goal.revision + 1)
        self._turn_session = session
        self._last_checkpoint_at = 0.0
        self._aborting = False
        if accepted is not None:
            # The turn's own prompt is the delivery: it is passed to agent.iter, so
            # its presence in the snapshot is the proof, just like a steer's event.
            self._event_seen.add(accepted.request_id)
        timings = self._turn_timings = TurnTimings()
        self._timing_session_id = session.id
        self._publish('turn_started', session_id=session.id)
        if composition.meter.window is None:
            self._publish(
                'notice',
                text='Context window is unknown; this request cannot be checked against the model limit.',
                tone='warn',
            )
        self._name_session(session, text)

        budget = StepBudget(max_steps=self._budget(), role='turn')
        agent = composition.agent
        status = 'ok'
        error = ''
        run_for_final: Any = None
        prompt = stored_content(text, accepted.images) if accepted is not None else user_content(text)

        # A turn that publishes nothing for a while says so once, from a task of its
        # own. Created immediately before the `try` that cancels it, so every failure
        # of the turn has a `finally` that stops the watcher.
        activity = asyncio.Event()
        self._turn_activity = activity
        activity.set()
        watcher = asyncio.get_running_loop().create_task(self._watch_turn_silence(activity))

        try:
            history = session.messages
            recovery = recover_history(history)
            if recovery.changed:
                history = recovery.messages
                session.messages = history
                composition.store.save_progress(session)
                self._publish('notice', text=recovery.notice, tone='warn')

            # The workspace this turn selected, snapshotted for as long as the run
            # lasts: tools, permissions and shell commands resolve against it, so a
            # switch accepted while the turn works cannot move the ground under it.
            with execution_workspace(composition.workspace_manager.root):
                async with agent.iter(
                    prompt,
                    message_history=history,
                    instructions=goal_instructions(session.goal),
                    capabilities=[
                        ProcessEventStream(handler=self._stream),
                        budget,
                        MessageTypes(initial=(accepted.origin or origin()) if accepted else origin()),
                        GoalQuestionGate(current_goal=lambda: session.goal, id='goal_root_gate'),
                    ],
                    model_settings=model_settings(composition.model_service),
                    usage_limits=UsageLimits(request_limit=None),
                ) as run:
                    self._run = run
                    self._pump_steering()
                    async for _node in timings.trace(run):
                        self._pump_steering()
                        self._checkpoint()
                        self._publish('debug_changed')
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
            timings.finish()
            run_for_final = self._run
            self._run = None
            self._turn_session = None
            # The watcher belongs to this turn and to nothing else: cancelled here it
            # can never speak for a turn that already ended.
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            self._turn_activity = None
            self._resolve_question(None)

        history = recover_history(history).messages
        session.messages = history
        confirmations = self._confirmations(run_for_final)
        try:
            composition.store.save(session, confirmations=confirmations)
        except Exception as exc:  # a conflicting winner keeps the turn alive
            from src.core.sessions.session_conflict_error import SessionConflictError

            if not isinstance(exc, SessionConflictError):
                raise
            composition.store.save_progress_result(session, confirmations=confirmations)
            latest = composition.store.load(session.id)
            if latest is not None:
                composition.bind_session(latest)
                self._publish(
                    'notice',
                    text='Session changed elsewhere; local progress was saved in a recovered session.',
                    tone='warn',
                )
        self._confirmed.update(confirmation.request_id for confirmation in confirmations)
        self._finish_confirmed(set(self._confirmed))
        if status in ('cancelled', 'error') or self._aborting:
            # A failed turn closes its rows for the same reason a cancelled one does:
            # nothing else will ever close them, and the next start is not a janitor
            # for this process's failure. `stopped` is the terminal state the table
            # allows from `claimed` — there is no failure state, and `interrupted` is
            # reserved for reconciling a process that died. See
            # `docs/technical/architecture/service-runtime.md`.
            self._stop_pending(session.id)
            self._aborting = False

        if budget.exhausted:
            self._publish(
                'notice',
                text=f'Step limit of {budget.max_steps} reached: the turn got one last call without tools.',
                tone='warn',
            )
        goal = session.goal
        if goal is not None and goal.status in ('armed', 'active') and status == 'ok':
            session.goal = replace(goal, status='active')
            composition.store.save_progress(session)
            self._goal_needs_review = True
        else:
            self._goal_needs_review = False
            if goal is not None and goal.status in ('armed', 'active') and status in ('cancelled', 'error'):
                session.goal = replace(goal, status='stopped' if status == 'cancelled' else 'blocked')
                composition.store.save_progress(session)
            self._publish('turn_finished', status=status, error=error, session_id=session.id)
        self._publish_state('turn')

    async def _review_goal(self) -> bool:
        """Review a settled goal once; return whether another turn was queued."""
        session = self.session
        goal = session.goal
        if goal is None or goal.status != 'active' or self._has_open_input() or self._closing:
            self._goal_needs_review = False
            return False
        revision = goal.revision
        if goal.continuations >= self.composition.settings.goal_max_continuations:
            session.goal = replace(goal, status='blocked')
            self.composition.store.save_progress(session)
            self._goal_needs_review = False
            self._publish('notice', text='Goal paused: automatic continuation limit reached.', tone='warn')
            self._publish('turn_finished', status='ok', error='', session_id=session.id)
            self._publish_state('goal')
            return False
        report = next(
            (
                message.text
                for message in reversed(session.messages)
                if isinstance(message, ModelResponse) and message.text
            ),
            '',
        )
        roadmap = '\n'.join(
            f'{item.status}: {item.title} {item.notes or ""}' for item in self.composition.roadmap_store.root.snapshot()
        )
        try:
            self._goal_review_task = asyncio.create_task(
                review_goal(
                    self.composition.current_model(),
                    GoalReviewInput(
                        goal=goal, roadmap=roadmap, report=report, agreements=conversation_context(session.messages)
                    ),
                    model_settings=model_settings(self.composition.model_service),
                )
            )
            decision = await self._goal_review_task
        except asyncio.CancelledError:
            if self._closing:
                raise
            return False
        except Exception as exc:  # noqa: BLE001 - report reviewer failure without claiming success
            decision = None
            error = str(exc)
        else:
            error = ''
        finally:
            self._goal_review_task = None
        current = session.goal
        if (
            self.session is session
            and current is not None
            and current.revision != revision
            and current.status in ('armed', 'active')
            and not self._has_open_input()
            and not self._subagent_working()
            and not self._closing
        ):
            session.goal = replace(current, status='active')
            self.composition.store.save_progress(session)
            self._goal_needs_review = True
            return True
        if (
            self.session is not session
            or current is None
            or current.revision != revision
            or current.status != 'active'
            or self._has_open_input()
            or self._subagent_working()
            or any(str(node.status) in ('done', 'failed') for node in self.composition.summoner.waiting())
            or self._closing
        ):
            self._goal_needs_review = False
            return False
        self._goal_needs_review = False
        if decision is None or decision.decision in ('needs_user', 'blocked'):
            session.goal = replace(current, status='blocked')
            self.composition.store.save_progress(session)
            reason = error if decision is None else decision.reason
            self._publish('notice', text=f'Goal paused: {reason}', tone='warn')
        elif decision.decision == 'complete':
            session.goal = replace(current, status='completed')
            self.composition.store.save_progress(session)
        else:
            key = (session.id, current.revision)
            if self._goal_progress_key != key:
                self._goal_progress = ProgressTracker()
                self._goal_progress_key = key
            if self._goal_progress.stalled(report, roadmap, decision.reason):
                session.goal = replace(current, status='blocked')
                self.composition.store.save_progress(session)
                self._publish('notice', text='Goal paused: repeated reviews show no observable progress.', tone='warn')
                self._publish('turn_finished', status='ok', error='', session_id=session.id)
                self._publish_state('goal')
                return False
            session.goal = replace(current, continuations=current.continuations + 1)
            self.composition.store.save_progress(session)
            text = f'Goal review: the objective is not complete. Continue: {decision.reason}'
            await self._accept_message(text, message_origin=origin('agent_message'))
            self._publish_state('goal')
            return True
        self._publish('turn_finished', status='ok', error='', session_id=session.id)
        self._publish_state('goal')
        return False

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
                from src.core.sessions.titling import generate_title

                title = await generate_title(self.composition.model_service.current_model(), text)
            except Exception:  # noqa: BLE001 - a title is a nicety
                return
            if not title:
                return
            session.title = title
            self.composition.store.save_progress(session)
            self._publish_state('session_named')

        task = asyncio.create_task(name())
        self._title_tasks.add(task)
        task.add_done_callback(self._title_tasks.discard)

    # --- the stream ----------------------------------------------------------

    async def _stream(self, _ctx: Any, events: Any) -> None:
        """Turns a pydantic-ai stream into events a browser can apply."""
        async for event in events:
            if self._turn_timings is not None:
                self._turn_timings.stream_event()
            self._note_activity()
            self._pump_steering()
            self._checkpoint()
            if isinstance(event, PartStartEvent):
                part = event.part
                if isinstance(part, ThinkingPart) and part.content:
                    self._publish('thinking_delta', text=part.content)
                elif isinstance(part, TextPart) and part.content:
                    self._publish('text_delta', text=part.content)
            elif isinstance(event, PartDeltaEvent):
                delta = event.delta
                if isinstance(delta, TextPartDelta) and delta.content_delta:
                    # The one event that must not be batched: it is the typing.
                    self._publish('text_delta', text=delta.content_delta)
                elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                    self._publish('thinking_delta', text=delta.content_delta)
            elif isinstance(event, FunctionToolResultEvent):
                ok = getattr(event.part, 'outcome', 'success') == 'success'
                content = present_output(event.part.tool_name, event.part.content).text
                self._publish(
                    'tool_result',
                    tool_name=event.part.tool_name,
                    call_id=str(getattr(event.part, 'tool_call_id', '')),
                    ok=ok,
                    preview=content[:PREVIEW_CHARS],
                )
            elif isinstance(event, EnqueuedMessagesEvent):
                if str(event.enqueue_id) in self._goal_enqueues:
                    for message in event.messages:
                        for part in message.parts:
                            if isinstance(part, UserPromptPart) and part in event.parts:
                                mark(message, part, origin('internal_notice'))
                    self._goal_enqueues.discard(str(event.enqueue_id))
                    self._checkpoint(force=True)
                request_id = self._request_for_enqueue(str(event.enqueue_id))
                if request_id is not None:
                    self._event_seen.add(request_id)
                    # Only a snapshot forced by the delivery event may confirm it.
                    self._checkpoint(force=True)
                self._publish('steer_delivered', enqueue_id=str(event.enqueue_id))

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
            message = matching_message(messages, item)
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
            self._publish(
                'tool_call',
                tool_name=event.tool_name,
                args=jsonable(event.args),
                invocation_id=getattr(event, 'invocation_id', ''),
            )
            return
        summary = getattr(event, 'summary', None)
        self._publish(
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

    # --- what the turn is doing ----------------------------------------------

    def _on_model_retry(self, notice: RetryNotice) -> None:
        """Publishes the retry the model is about to attempt, and never raises.

        The terminal announces the same fact in its chat (`SigilApp.on_model_retry_notice`);
        the service had no listener at all, so a provider that retried published
        nothing and the screen showed "Thinking" for minutes. It reuses the terminal's
        sentence, so the two clients say the same thing about the same wait.

        Notifying is not worth failing: the capability already protects the retry from
        a broken callback, and this keeps the guarantee local to the sink.
        """
        self._note_activity()
        try:
            self._publish(
                'notice',
                text=(
                    f'↻ Transient model failure ({notice.reason}); '
                    f'retry {notice.next_attempt}/{notice.max_attempts} in {notice.delay:.1f}s'
                ),
                tone='warn',
            )
        except Exception:  # noqa: BLE001, S110 - notifying must never break the retry
            pass

    def _note_activity(self) -> None:
        """Marks that the turn said something, so the slow-turn notice keeps quiet."""
        activity = self._turn_activity
        if activity is not None:
            activity.set()

    async def _watch_turn_silence(self, activity: asyncio.Event) -> None:
        """Says once that the turn is still waiting, then stops watching.

        The wait is the same bounded-event pattern the consumer uses, so nothing
        blocks and every event the turn publishes restarts the window. There is no
        deadline behind it: when it fires, the turn goes on.
        """
        while not self._closing:
            activity.clear()
            try:
                await asyncio.wait_for(activity.wait(), timeout=SLOW_TURN_NOTICE_SECONDS)
            except TimeoutError:
                if self._pending_question is not None:
                    # That wait is a person's, it has its own deadline, and saying
                    # "waiting on the model" about it would be a lie.
                    continue
                self._publish(
                    'notice',
                    text=(
                        f'Still waiting on the model: no output for {SLOW_TURN_NOTICE_SECONDS:g} seconds. '
                        'The turn is still running.'
                    ),
                    tone='warn',
                )
                return

    # --- questions -----------------------------------------------------------

    async def _ask(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        """Publishes the questions and waits for the browser to answer them.

        The wait is indefinite by default. An explicitly configured deadline withdraws
        the question and cancels the turn without inventing an answer.
        """
        self._question_ids += 1
        pending = PendingQuestion(id=f'q{self._question_ids}', questions=questions)
        self._pending_question = pending
        self._publish(
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
            # `asyncio.wait` rather than `wait_for`: the latter cancels the future it
            # was given on timeout, and a question that is withdrawn is not a question
            # whose answer was cancelled. Nothing touches the future but `answer`.
            done, _ = await asyncio.wait({pending.future}, timeout=self._question_timeout)
        except asyncio.CancelledError:
            self._pending_question = None
            if not pending.future.done():
                pending.future.cancel()
            raise
        if pending.future in done:
            return pending.future.result()
        self._expire_question(pending)
        return None

    def _expire_question(self, pending: PendingQuestion) -> None:
        """Withdraws a question nobody answered, visibly.

        The client gets two events because they answer different needs: the close
        retires the panel, which the id alone does, and the notice says why, which is
        the part a person coming back to the screen cannot infer from an empty prompt.
        """
        if self._pending_question is pending:
            self._pending_question = None
        if not pending.future.done():
            pending.future.set_result(None)
        self._publish('question_closed', id=pending.id, reason='timeout')
        self._publish(
            'notice',
            text=(
                f'No answer to the question after {self._question_timeout:g} seconds, so it was withdrawn '
                'and the turn stopped. What the agent asked is above; send your message again to continue.'
            ),
            tone='warn',
        )

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
        self._publish('question_closed', id=question_id, reason='answered' if parsed is not None else 'cancelled')
        return True

    def _resolve_question(self, answers: list[QuestionAnswer] | None, *, reason: str = 'withdrawn') -> None:
        """Releases a waiting question with nothing: the turn is not going to use it."""
        pending = self._pending_question
        if pending is None:
            return
        self._pending_question = None
        if not pending.future.done():
            pending.future.set_result(answers)
        self._publish('question_closed', id=pending.id, reason=reason)

    @property
    def pending_question(self) -> PendingQuestion | None:
        return self._pending_question

    # --- tree ----------------------------------------------------------------

    def _on_tree_change(self, node: Any) -> None:
        self._publish('tree_changed', node_id=getattr(node, 'id', ''))
        if self._goal_needs_review and not self._subagent_working() and not self._closing:
            self._settled.clear()
            self._wake.set()

    def _on_tree_idle(self) -> None:
        """A subagent answered with nobody in a turn: that is work, and it is owed.

        The engine calls this once per answer that found no live run. Publishing the
        state is the old half; the new one is telling the consumer, because the wake
        is a turn and the consumer is the only thing that starts turns. It also
        clears `_settled`: `wait()` means "nothing left to do", and a report nobody
        has read is something left to do.
        """
        self._publish_state('tree_idle')
        if self._closing or self._consumer is None:
            return
        if not self.composition.summoner.waiting():
            return
        self._settled.clear()
        self._wake.set()

    # --- tree and session settings -------------------------------------------

    def node(self, ref: str) -> AgentNode:
        """The node a reference names, accepting a unique prefix of its id.

        The resolution is the summoner's, so a client addresses a node with the
        same shorthand the terminal's tree shows. The failure is translated to a
        lookup because "no such node" is a request that names nothing, not a
        session one has to wait for.

        Raises:
            LookupError: when nothing in this session's tree matches.
        """
        try:
            return self.composition.summoner.resolve(ref)
        except SummonError as exc:
            raise LookupError(str(exc)) from exc

    def stop_node(self, node_id: str) -> tuple[AgentNode, str]:
        """Stops a node and everything it summoned.

        Returns the node and the sentence the terminal paints for it.

        Raises:
            LookupError: when no node has that reference.
            SummonError: when the node cannot be stopped, the session's agent most
                of all.
        """
        node = self.node(node_id)
        return node, self.composition.stop_subagent(node.id)

    def background_node(self, node_id: str) -> tuple[AgentNode, str]:
        """Sends a running node to the background: it keeps working, answers later.

        Raises:
            LookupError: when no node has that reference.
            SummonError: when the node is the session's agent or already finished.
        """
        node = self.node(node_id)
        return node, self.composition.background_subagent(node.id)

    async def compact(self, mode: Literal['deterministic', 'llm']) -> ManualCompactionResult:
        """Reserve this session while a manual projection is prepared and committed."""
        from src.core.context.compaction.manual import compact
        from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy

        self._refuse_while_busy('compact the context')
        if not self._settled.is_set() or self._subagent_working() or self._has_open_input():
            raise RuntimeError('Wait for pending work to finish before compacting the context.')
        strategy = self.composition.context_capability._strategy_for_now()
        if not isinstance(strategy, AppendOnlyStrategy):
            raise RuntimeError('Manual compaction requires the append-only context strategy.')  # noqa: TRY004
        self._compacting = True
        self._compaction_done.clear()
        try:
            session = self.session
            original_messages = session.messages
            candidate = replace(session, messages=list(original_messages))
            selected = self.composition.root_run_model()
            try:
                result = await compact(
                    candidate.messages,
                    strategy,
                    self.composition.meter.budget(),
                    mode=mode,
                    model=selected.model,
                    model_settings=selected.settings,
                )
            except BaseException:
                self._commit_compaction(session, original_messages, candidate)
                raise
            self._commit_compaction(session, original_messages, candidate)
            return result
        finally:
            self._compacting = False
            self._compaction_done.set()
            self._wake.set()

    def _has_open_input(self) -> bool:
        return (
            self.composition.store.database.one(
                "SELECT 1 FROM accepted_inputs WHERE session_id = ? AND state IN ('accepted', 'claimed', 'delivered') LIMIT 1",
                (self.session.id,),
            )
            is not None
        )

    def _commit_compaction(self, session: Any, original: list[Any], candidate: Any) -> None:
        if candidate.messages == original:
            return
        if self.session is not session or session.messages is not original or self._has_open_input():
            raise RuntimeError('The session changed during compaction; try again.')
        self.composition.store.save(candidate)
        session.messages = candidate.messages
        session.updated_at = candidate.updated_at
        session._persisted_at = candidate._persisted_at
        session._baseline = candidate._baseline
        session._draft = candidate._draft
        self._publish_state('context')

    def apply_goal(self, command: GoalCommand) -> GoalUpdate:
        """Apply a goal update to this session, including a running turn."""
        session = self.session
        update = apply_goal_command(command, session.goal, question_pending=self._pending_question is not None)
        if update.refused:
            raise RuntimeError(update.notice)
        if not update.changed:
            return update
        previous = session.goal
        session.goal = update.goal
        try:
            self.composition.store.save(session)
        except Exception:
            session.goal = previous
            raise
        run = self._run
        if run is not None:
            instruction = goal_instructions(update.goal) or 'The user stopped the session goal mode.'
            try:
                part = UserPromptPart(instruction)
                request = ModelRequest(parts=[part])
                mark(request, part, origin('internal_notice'))
                enqueue_id = run.enqueue(request)
            except Exception as exc:  # noqa: BLE001 - goal remains saved; report failed steering
                self._publish(
                    'notice', text=f'Goal saved, but the running turn could not receive it: {exc}', tone='warn'
                )
            else:
                if enqueue_id is not None:
                    self._goal_enqueues.add(str(enqueue_id))
        self._publish_state('goal')
        return update

    def set_external_files(self, allow: bool) -> Any:
        """Allows or denies file access outside the workspace for this session.

        Refused mid-turn, like the terminal's `/external-files`: the flag is read
        per tool call, and a turn that is already running would change the boundary
        under a call it has not made yet.

        Raises:
            RuntimeError: while a turn is running.
        """
        self._refuse_while_busy('change file access')
        session = self.composition.apply_external_files(allow)
        self._publish_state('session')
        return session

    async def toggle_mcp(self, name: str, enabled: bool) -> Any:
        """Enables or disables one MCP server for this session.

        Refused mid-turn and while a subagent runs, the two criteria the terminal
        applies to `/mcp`.

        Raises:
            RuntimeError: while a turn or a subagent is running.
            LookupError: when the config declares no server with that name.
        """
        self._refuse_while_busy('change MCP servers')
        session = await self.composition.apply_mcp_override(name, enabled)
        self._publish_state('mcp')
        return session

    # --- selection and history -----------------------------------------------

    def set_model(self, account: str, model: str, thinking: str = 'default') -> Any:
        """Switches the model of the session.

        The decision is `ModelService.select`, which refuses while a turn runs:
        the refusal is the engine's, not a rule repeated here. The agent is
        rebuilt afterwards because it closes over the model it was built with, so
        without it the change would be visible in the projection and nowhere else.

        Raises:
            BusyError: while a turn is running.
            ProviderError: when the account, the model or the level is refused.
        """
        snapshot = self.composition.model_service.select(account, model, thinking)
        self.composition.apply_model_change(snapshot)
        self.composition.rebuild_agent()
        self._publish_state('model')
        return snapshot

    def set_effort(self, thinking: str) -> Any:
        """Sets the reasoning level of the active model, effective on the next step.

        `model_settings` is read when a turn starts, so nothing else has to move:
        the run already in flight keeps the level it began with, which is what the
        terminal does when it lets `/effort` through.

        Raises:
            NoAccountError: when nothing is configured.
            BusyError: while a turn is running.
            ProviderError: when the model does not accept that level.
        """
        snapshot = self.composition.model_service.set_thinking(thinking)
        self.composition.apply_model_change(snapshot)
        self._publish_state('model')
        return snapshot

    def select_agent(self, name: str) -> AgentProfile:
        """Activates an agent profile for the next turn.

        Refused while a turn runs, like the terminal's `/agent`: an agent swap
        changes the instructions *and* which tools exist in a run whose tool set
        was already computed. The profile's own model default, if it declares one,
        is applied through the same path as `/model`.

        Raises:
            RuntimeError: while a turn is running.
            AgentNotFound: when no selectable profile has that name.
            ProviderError: when the profile's model configuration is invalid.
        """
        self._refuse_while_busy('switch agent')
        self.composition.agent_registry.refresh()
        profile, _snapshot = self.composition.apply_agent(name)
        self._publish_state('agent')
        return profile

    def rollback_points(self) -> list[Checkpoint]:
        """The turns the open session can return to, newest first.

        The list is the engine's (`composition.rollback`, the same object the
        terminal uses), so both clients offer the same points in the same order.
        """
        return self.composition.rollback.points(self.session.id)

    def rollback(self, turn_id: str, *, files: bool = True) -> RollbackOutcome:
        """Puts the session back to before ``turn_id``, rows included.

        Blocks: it diffs and restores the workspace. The route runs it off the
        event loop, the way the terminal runs it off its worker.

        Raises:
            RuntimeError: while a turn is running.
            RollbackError: when the turn is not a point this session can reach.
        """
        self._refuse_while_busy('roll back')
        outcome = self.composition.rollback.to_turn(self.session.id, turn_id, files=files)
        self._reload_session()
        self._publish_state('rollback')
        return outcome

    def fork(self, turn_id: str) -> ForkOutcome:
        """Branches a new session at the instant before ``turn_id``.

        Nothing is destroyed and the workspace is not touched: the session stays
        open and the new one is reported, exactly as the terminal's picker does.

        Raises:
            RuntimeError: while a turn is running.
            RollbackError: when the turn is not a fork point of this session.
        """
        self._refuse_while_busy('fork')
        return self.composition.rollback.fork_to(self.session.id, turn_id)

    def create_worktree(self, name: str) -> Any:
        """Cuts a worktree and moves the service into it, like `/new --worktree`.

        Blocks: it runs git and changes the process directory, so the route calls it
        off the event loop the way the terminal calls it off its worker.

        Raises:
            RuntimeError: while a turn is running.
            WorkspaceError: when the name is unusable or git refuses.
        """
        self._refuse_while_busy('create a worktree')
        switch = self.composition.workspace_manager.create_and_switch(name)
        self.composition.root = switch.workspace.root
        self._publish_state('workspace')
        return switch

    def refuse_while_busy(self, action: str) -> None:
        """The busy refusal, for a route that must answer before slow work.

        An account route reaches the network to check a credential; being told the
        session is busy only after that call would be a wait for nothing.

        Raises:
            RuntimeError: while a turn is running.
        """
        self._refuse_while_busy(action)

    def adopt_model(self, snapshot: Any) -> None:
        """Applies a model change the engine already made and republishes the shell.

        `add_api_key` selects its new account itself, so the agent has to be rebuilt
        here the way `set_model` does it: the agent closes over the model it was
        built with, and without this the projection would change and the next turn
        would not.
        """
        self.composition.apply_model_change(snapshot)
        self.composition.rebuild_agent()
        self._publish_state('model')

    def _refuse_while_busy(self, action: str) -> None:
        """The one refusal for the history commands: a turn is still running."""
        if self.busy:
            raise RuntimeError(f'A turn is running: finish or stop it before you {action}.')

    def _reload_session(self) -> None:
        """Rebinds the open session to its rows after a rollback shortened them.

        The rollback wrote behind the session object's back; the rows are the
        truth now, so the object the server answers from is replaced by the one
        the store loads. A rollback that removed every message leaves nothing to
        load, and the session keeps the object it has.
        """
        reloaded = self.composition.store.load(self.session.id)
        if reloaded is not None:
            # The rows moved; the model did not. Resolving it here would let a
            # rollback change which model the conversation runs on.
            self.composition.bind_session(reloaded, model=False)

    # --- the model defaults --------------------------------------------------

    def model_defaults(self) -> ModelDefaultsState:
        """What the two config files say about default models, per file.

        Read through the composition, which re-reads the files on every call: the
        screen writes one of them and then shows the result, and a copy kept here
        would answer with its own memory instead of what is on disk — including a
        hand edit made while the service runs.

        Never refused, even mid-turn: this is a projection, and nothing it reads
        changes under a running turn.
        """
        return self.composition.model_defaults_state()

    def save_model_default(
        self, target: str | None, config: AgentModelConfig | None, scope: ConfigScope
    ) -> ModelDefaultsState:
        """Writes one default and answers with the state the write left behind.

        ``target`` is an agent name, or ``None`` for the block a conversation starts
        on; ``config`` is ``None`` to clear the entry.

        Refused mid-turn, the rule the terminal's `/default-models` already applies
        (`BUSY_COMMANDS`): a subagent reads `agents.<name>` the moment it is
        summoned, so rewriting that entry under a running turn would change the model
        of a branch that has not started yet. The subagent criterion belongs to
        `/mcp`, and this command does not apply it, so it is not added here.

        Raises:
            RuntimeError: while a turn is running, or when the engine refused the
                write; in that case the message is the engine's own sentence.
        """
        self._refuse_while_busy('change the model defaults')
        errors = self.composition.save_model_default(target, config, scope)
        if errors:
            raise RuntimeError(' '.join(errors))
        return self.composition.model_defaults_state()

    # --- workspace -----------------------------------------------------------

    def switch_workspace(self, root: str) -> dict[str, Any]:
        """Moves the server to another workspace and starts an empty conversation.

        Refused mid-turn: `os.chdir` while a tool is running would make the tool read
        a different tree than the one it started in.
        """
        self._refuse_while_busy('switch workspace')
        switch = self.composition.workspace_manager.switch(root)
        self.composition.root = switch.workspace.root
        session = self.composition.store.draft_session()
        # A workspace switch starts a conversation, it does not choose one: the user
        # opens a stored conversation with `/load` or `/api/session/open`, and that is
        # where its own model comes back with it. Reopening the last one here decided
        # for them. Binding the draft resolves the model the way `new_session` does —
        # the agent's own, then `default_model`, then the last one used.
        self.composition.bind_session(session)
        self.composition.rebuild_agent()
        self._publish_state('workspace')
        return {'root': str(switch.workspace.root), 'label': switch.workspace.label, 'changed': switch.changed}

    def new_session(self) -> Any:
        """Starts an empty conversation in the current workspace."""
        self._refuse_while_busy('start a session')
        session = self.composition.store.draft_session()
        session.agent = self.composition.agent_registry.active_name
        # Binding resolves and records the model: the agent's own, then
        # `default_model`, then the last one used. A model chosen later belongs to
        # this session only.
        self.composition.bind_session(session)
        session.selection = session.selection or self.composition.model_service.snapshot().selection
        self._publish_state('session')
        return session

    def open_session(self, session_id: str) -> Any:
        """Opens an existing conversation, wherever its workspace is."""
        self._refuse_while_busy('open another session')
        target = self.composition.store.load(session_id)
        if target is None:
            raise LookupError(f'No session {session_id}.')
        if target.cwd != self.composition.store.cwd:
            self.composition.workspace_manager.switch(target.cwd)
            self.composition.root = target.cwd
        self.composition.bind_session(target)
        self._publish_state('session')
        return target

    # --- leaving -------------------------------------------------------------

    async def aclose(self) -> None:
        """Finish session cleanup even if the caller is cancelled."""
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(self) -> None:
        self.close()
        if self._consumer is not None:
            await asyncio.gather(self._consumer, return_exceptions=True)
        if self._title_tasks:
            await asyncio.gather(*self._title_tasks, return_exceptions=True)
        await self.composition.summoner.cancel_all_and_wait()
        try:
            if self._mcp_task is not None:
                await self._mcp_task
        finally:
            await self.composition.mcp.aclose()

    def close(self) -> None:
        """Releases this session's execution, and only that.

        A question still open here is released first. `abort` and the turn's own
        `finally` do that on their paths, but a process that leaves while a question
        waits — a graceful stop, a failing start — would otherwise leave a future
        nobody will ever resolve, and the task awaiting it would be collected still
        waiting. Closing it with nothing is the same value every other withdrawal
        uses, so the caller takes the path it already knows.

        The callbacks this session registered are detached for the same reason: a
        closed session must not be called by a tool, a model retry or a tree event.

        What the process owns is deliberately absent here. The ownership lock and
        the shared cached database outlive a session, so they are released by
        `ServerRuntime.close`, in a `finally`, whether or not this session was ever
        started.
        """
        self._resolve_question(None, reason='closed')
        self._closing = True
        if self._consumer is not None and not self._consumer.done():
            try:
                self._consumer.cancel()
            except RuntimeError:  # pragma: no cover - the loop already stopped
                pass
        for task in self._title_tasks:
            task.cancel()
        self._detach_callbacks()

    def _detach_callbacks(self) -> None:
        """Detaches the engine callbacks, idempotently: closing twice is harmless.

        Registrations made in ``__init__`` are undone with their owner's default,
        so a closed session is not asked to publish tool events, notices, retries or
        tree changes, and the model service stops refusing swaps on its behalf.

        ``on_idle(None)`` is not a blanket listener reset: it re-registers the
        composition's own tree relay with no session callback, so the engine keeps
        announcing changes to whoever attaches next.
        """
        self.composition.extension_notice = None
        self.composition.context_capability.on_unknown_window = None
        self.composition.question_hub.set_asker(None)
        self.composition.model_service.is_busy = _not_busy
        self.composition.tool_logger.remove_sink(self._on_tool_event)
        self.composition.model_retry.on_retry = None
        self.composition.on_idle(None)
        self.composition.tree_changed = None


__all__ = ['SessionRuntime']
