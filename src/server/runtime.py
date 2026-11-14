"""The service boundary: what the process owns, and what one session runs.

The service is two lifetimes. The process holds an exclusive database lock, one
store, one event hub and the idle decision that stops it; one session owns the
turn loop, the queue it claims, the questions it waits on and the callbacks the
engine calls. `ServerRuntime` is the process side and `SessionRuntime` — in
`src.server.session_runtime` — is the session side. The turn-loop rationale that
used to be documented here lives there, next to the code it explains.

Nothing about the public API moved: routes, the CLI and the existing tests still
import `ServerRuntime`, the support types and the constants from this module, and
the facade's methods are explicit, typed delegations with the signatures they had.
The shared values themselves are defined in `src.server.runtime_support`, so the
session module can use them without importing the facade back.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from typing import Any

from src.engine.agents.model_config import AgentModelConfig
from src.engine.agents.model_defaults import ConfigScope, ModelDefaultsState
from src.engine.agents.profile.models.agent_profile import AgentProfile
from src.engine.agents.rollback import Checkpoint, ForkOutcome, RollbackOutcome
from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.bootstrap import Composition
from src.engine.storage.accepted_inputs import AcceptedInputs
from src.server.diagnostics import record
from src.server.events import EventHub
from src.server.ownership import OwnershipError, ServerOwnership

# Re-exported for the importers that already name these here: the routes, the CLI,
# the terminal client and the existing tests. The definitions live in
# `runtime_support`, and `__all__` names them so the re-export is deliberate rather
# than an unused import.
from src.server.runtime_support import (
    CHECKPOINT_INTERVAL,
    DEFAULT_IDLE_GRACE,
    IDLE_POLL_INTERVAL,
    MAX_IMAGES,
    PREVIEW_CHARS,
    QUESTION_TIMEOUT_ENV,
    QUESTION_TIMEOUT_SECONDS,
    QUEUE_POLL_INTERVAL,
    SLOW_TURN_NOTICE_SECONDS,
    WAKE_PROMPT,
    IncomingImage,
    PendingQuestion,
    decode_images,
    resolve_question_timeout,
)
from src.server.session_runtime import SessionRuntime


class IdleShutdown:
    """Decides when a service that has had no work for a while should stop.

    Two facts keep the service alive: a turn that is running, and any queue row
    that is not terminal — in *any* session, because the service serves one
    database, not one browser. Connected event subscribers are deliberately not on
    that list: a page can reconnect to whatever service is there, so an open tab is
    not a reason for a process to outlive the work.

    A pending question needs no rule of its own. The agent asks from inside the
    turn that is waiting for the answer, so the turn is still running and the
    question is covered by the first fact; there are no interactive approvals.

    The clock and the work check are arguments, so a test advances the window
    instead of sleeping through it, and the thirty-minute decision costs nothing.
    """

    def __init__(
        self,
        has_work: Callable[[], bool],
        *,
        grace: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._has_work = has_work
        self._grace = grace
        self._clock = clock
        self._idle_since: float | None = None

    @property
    def grace(self) -> float:
        """Seconds of no work this decision waits for."""
        return self._grace

    def expired(self) -> bool:
        """Whether the service has gone ``grace`` seconds without work.

        Called repeatedly: the first look starts the window, work drops it and
        starts it again later, so a burst of short turns never adds up to a
        shutdown. A work check that raises is not proof of idleness — the exception
        propagates, and the caller decides that an unanswerable question means
        "keep serving".
        """
        now = self._clock()
        if self._has_work():
            self._idle_since = None
            return False
        if self._idle_since is None:
            self._idle_since = now
        return now - self._idle_since >= self._grace


class ServerRuntime:
    """Owns the process for one browser session and delegates the session's work.

    The workspace is process state, not client state: `os.chdir` is global, so two
    browsers share the directory the tools read. That is the same model the terminal
    has (one process, one workspace), and it is the honest one until the engine stops
    resolving paths against the process cwd.

    This object keeps what outlives work: the ownership lock and the gate that asks
    whether it is held, the reconciliation `start` performs, the accepted-inputs
    handle, the hub and the service-wide idle decision. Everything a turn does is
    delegated — unchanged, and explicitly — to the one `SessionRuntime` exposed by
    `session_runtime`. There is no registry here: one runtime, one session, until
    the concurrency work says otherwise.
    """

    def __init__(
        self,
        composition: Composition,
        hub: EventHub,
        *,
        require_ownership: bool = True,
        ownership: ServerOwnership | None = None,
        question_timeout: float | None = None,
    ) -> None:
        self.composition = composition
        self.hub = hub
        self._require_ownership = require_ownership
        # An ownership taken before this runtime existed, or one for it to take.
        # Adopting is what lets `cli.run` hold the lock across `Composition`: `flock`
        # treats two descriptors of one process as two holders, so opening the same
        # file here would refuse this very process after it had already paid to
        # compose the engine. `acquire` on an object that already holds it does
        # nothing, so `start` needs no special case and no second descriptor is
        # ever opened.
        self._ownership = ownership if ownership is not None else ServerOwnership(composition.store.database.path)
        self._started = False
        self._closing = False

        # Durable consumer. The queue is the source of truth for accepted work, and
        # it belongs to the process: `start` reconciles it and the idle decision
        # reads it, whether or not a session is running.
        self._accepted = AcceptedInputs(composition.store.database)
        # The deadline is resolved once, here: a question asked while the service
        # runs cannot change how long it is allowed to wait. `question_timeout` is
        # the testable seam, and the environment is the operator's.
        self._session_runtime = SessionRuntime(
            composition,
            hub,
            self._accepted,
            question_timeout=resolve_question_timeout(question_timeout),
            require_worker_ownership=self._require_worker_ownership,
        )

    # --- state ---------------------------------------------------------------

    @property
    def session(self) -> Any:
        return self._session_runtime.session

    @property
    def busy(self) -> bool:
        return self._session_runtime.busy

    @property
    def pending_question(self) -> PendingQuestion | None:
        return self._session_runtime.pending_question

    @property
    def session_runtime(self) -> SessionRuntime:
        """The one session this service drives. Read-only: replacing it is not a command."""
        return self._session_runtime

    def has_active_work(self) -> bool:
        """Whether stopping now would cut something short.

        A running turn or background agent is work in this process. A non-terminal
        row is work the database says exists, including rows another session accepted, because the
        service serves the whole database. A connected event subscriber is not work:
        it reconnects to whatever service is there.
        """
        return self.busy or self.composition.summoner.running() > 0 or self._accepted.has_open()

    async def stop_when_idle(
        self,
        request_shutdown: Callable[[], None],
        *,
        grace: float,
        poll: float = IDLE_POLL_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Asks for a graceful stop after ``grace`` seconds with no work left.

        The request is the only thing this does: it does not abandon a turn, stop a
        row or touch the endpoint file. Ending the serving loop is what runs the
        lifespan shutdown, which unpublishes the endpoint, closes the runtime and
        lets go of the ownership lock — in that order, through the path every other
        exit already uses.

        A work check that raises keeps the service alive and says so once per
        streak: a database that cannot answer is not proof that nothing is left to
        do, and a service that dies because its store hiccuped is worse than one
        that lives an extra window.
        """
        decision = IdleShutdown(self.has_active_work, grace=grace, clock=clock)
        # `poll` bounds how often the queue is asked, never how long the window is
        # observed late: a 50 ms window looked at every 5 s would make the flag a lie.
        interval = poll if decision.grace <= 0 else min(poll, decision.grace)
        complaining = False
        while not self._closing:
            try:
                idle = decision.expired()
            except Exception as exc:  # noqa: BLE001 - a failed question is not a stop
                if not complaining:
                    complaining = True
                    record('server_idle_check_failed', error=exc)
                    self.hub.publish('notice', text=f'Idle check: {exc}', tone='warn')
                await asyncio.sleep(interval)
                continue
            complaining = False
            if idle:
                record(
                    'server_idle_shutdown', grace_seconds=grace, background_agents=self.composition.summoner.running()
                )
                request_shutdown()
                return
            await asyncio.sleep(interval)

    # --- lifecycle -----------------------------------------------------------

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

    def _require_worker_ownership(self) -> None:
        """The gate the session asks before starting its workers.

        The consumer and the MCP loader both touch the store, so both need the
        lock. It stays here because the lock is process state; the session receives
        this method as a callback and never calls `ServerOwnership` itself.
        """
        if self._require_ownership and not self._ownership.held:
            raise OwnershipError('The queue consumer requires the database lock')

    def close(self) -> None:
        """Releases everything, and leaves the queue exactly as it found it.

        Interruption is crash reconciliation, and it belongs to `start`: a process
        that is leaving on purpose must not claim that the rows it never executed
        were lost. A row can land between the last idle look and this call, and
        marking it ``interrupted`` would be a lie the next run would act on.

        The session closes first — its question, its consumer and its callbacks —
        and the process resources go in a `finally`, so even a session that fails to
        close cannot leave the store locked. The ownership is released last, so the
        store is never writable by a second process while this one is still closing.
        """
        try:
            self._session_runtime.close()
        finally:
            self._closing = True
            self._started = False
            # Released whether this runtime took the lock or adopted one taken
            # before it existed, and whether or not `start` ever ran: a runtime that
            # exists but never served must not keep the store locked, and the process
            # that composed the engine is waiting on exactly this. `release` is
            # idempotent, so closing twice is a no-op rather than a double release.
            self._ownership.release()
            self.composition.close()

    # --- delegated to the session --------------------------------------------

    def ensure_workers(self) -> None:
        """Starts the session's consumer and MCP loader behind the ownership gate."""
        self._session_runtime.ensure_workers()

    async def send(
        self, text: str, request_id: str | None = None, images: Sequence[IncomingImage] = ()
    ) -> dict[str, Any]:
        """Accepts a message durably; the session's consumer turns it or steers it."""
        return await self._session_runtime.send(text, request_id, images)

    def abort(self) -> bool:
        """Stops the turn, if there is one. The session owns the run and its rows."""
        return self._session_runtime.abort()

    def stop_all(self) -> bool:
        """Stops the turn and every subagent the session still runs."""
        return self._session_runtime.stop_all()

    async def wait(self) -> None:
        """Waits until the session's consumer has no accepted or running work."""
        await self._session_runtime.wait()

    def answer(self, question_id: str, answers: list[dict[str, Any]] | None) -> bool:
        """Resolves the session's pending question. ``None`` means the user cancelled."""
        return self._session_runtime.answer(question_id, answers)

    def node(self, ref: str) -> AgentNode:
        """The node a reference names, accepting a unique prefix of its id."""
        return self._session_runtime.node(ref)

    def stop_node(self, node_id: str) -> tuple[AgentNode, str]:
        """Stops a node and everything it summoned, and returns the terminal's sentence."""
        return self._session_runtime.stop_node(node_id)

    def background_node(self, node_id: str) -> tuple[AgentNode, str]:
        """Sends a running node to the background: it keeps working, answers later."""
        return self._session_runtime.background_node(node_id)

    def set_external_files(self, allow: bool) -> Any:
        """Allows or denies file access outside the workspace for this session."""
        return self._session_runtime.set_external_files(allow)

    async def toggle_mcp(self, name: str, enabled: bool) -> Any:
        """Enables or disables one MCP server for this session."""
        return await self._session_runtime.toggle_mcp(name, enabled)

    def set_model(self, account: str, model: str, thinking: str = 'default') -> Any:
        """Switches the session's model, through the engine's own refusal."""
        return self._session_runtime.set_model(account, model, thinking)

    def set_effort(self, thinking: str) -> Any:
        """Sets the reasoning level of the active model, effective on the next step."""
        return self._session_runtime.set_effort(thinking)

    def select_agent(self, name: str) -> AgentProfile:
        """Activates an agent profile for the next turn."""
        return self._session_runtime.select_agent(name)

    def rollback_points(self) -> list[Checkpoint]:
        """The turns the open session can return to, newest first."""
        return self._session_runtime.rollback_points()

    def rollback(self, turn_id: str, *, files: bool = True) -> RollbackOutcome:
        """Puts the session back to before ``turn_id``, rows included."""
        return self._session_runtime.rollback(turn_id, files=files)

    def fork(self, turn_id: str) -> ForkOutcome:
        """Branches a new session at the instant before ``turn_id``."""
        return self._session_runtime.fork(turn_id)

    def create_worktree(self, name: str) -> Any:
        """Cuts a worktree and moves the service into it, like `/new --worktree`."""
        return self._session_runtime.create_worktree(name)

    def refuse_while_busy(self, action: str) -> None:
        """The busy refusal, for a route that must answer before slow work."""
        self._session_runtime.refuse_while_busy(action)

    def adopt_model(self, snapshot: Any) -> None:
        """Applies a model change the engine already made and republishes the shell."""
        self._session_runtime.adopt_model(snapshot)

    def model_defaults(self) -> ModelDefaultsState:
        """What the two config files say about default models, per file."""
        return self._session_runtime.model_defaults()

    def save_model_default(
        self, target: str | None, config: AgentModelConfig | None, scope: ConfigScope
    ) -> ModelDefaultsState:
        """Writes one default and answers with the state the write left behind."""
        return self._session_runtime.save_model_default(target, config, scope)

    def switch_workspace(self, root: str) -> dict[str, Any]:
        """Moves the service to another workspace and starts an empty conversation."""
        return self._session_runtime.switch_workspace(root)

    def new_session(self) -> Any:
        """Starts an empty conversation in the current workspace."""
        return self._session_runtime.new_session()

    def open_session(self, session_id: str) -> Any:
        """Opens an existing conversation, wherever its workspace is."""
        return self._session_runtime.open_session(session_id)


__all__ = [
    'CHECKPOINT_INTERVAL',
    'DEFAULT_IDLE_GRACE',
    'IDLE_POLL_INTERVAL',
    'MAX_IMAGES',
    'PREVIEW_CHARS',
    'QUESTION_TIMEOUT_ENV',
    'QUESTION_TIMEOUT_SECONDS',
    'QUEUE_POLL_INTERVAL',
    'SLOW_TURN_NOTICE_SECONDS',
    'WAKE_PROMPT',
    'IncomingImage',
    'PendingQuestion',
    'ServerRuntime',
    'decode_images',
    'resolve_question_timeout',
]
