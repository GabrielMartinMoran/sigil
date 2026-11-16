"""The service facade and the one session runtime it owns.

Two boundaries live here, each proven at the cheapest level that can prove it:

- **The contract** (`@contract`): `ServerRuntime` keeps its imports, its
  constructor and its public method signatures, and the execution state it
  delegates belongs to a distinct, stable `SessionRuntime`.
- **The lifecycle** (`@integration`): closing a session cannot release the
  process lock or close the shared cached database, and closing the facade
  releases that lock once without interrupting rows it never executed. It does
  detach every callback it registered, and those registrations are the other
  half of the same boundary: what a closed session may still be asked to do. A
  real temporary database, a real `flock` and a real `Composition` are the
  contract here; a mock of either would prove nothing about a lifetime.

The turn path itself is proven by `tests/test_server_runtime.py`,
`tests/test_server_queue.py` and `tests/test_server_idle.py`. This file adds the
ownership split, not a second copy of the behaviour those files already oracle.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from src.core.agents.retry_notice import RetryNotice
from src.core.bootstrap import Composition
from src.core.interaction.models.question_item import QuestionItem
from src.core.interaction.models.question_option import QuestionOption
from src.core.settings import load_settings
from src.core.storage.accepted_inputs import AcceptedInputs
from src.core.tooling.tool_call_event import ToolCallEvent
from src.server import session_runtime as session_runtime_module
from src.server.event_hub import EventHub
from src.server.ownership_error import OwnershipError
from src.server.server_ownership import ServerOwnership
from src.server.server_runtime import ServerRuntime
from src.server.session_runtime import SessionRuntime

DELEGATED_METHODS = [
    'ensure_workers',
    'send',
    'abort',
    'stop_all',
    'wait',
    'answer',
    'node',
    'stop_node',
    'background_node',
    'set_external_files',
    'toggle_mcp',
    'set_model',
    'set_effort',
    'select_agent',
    'rollback_points',
    'rollback',
    'fork',
    'create_worktree',
    'refuse_while_busy',
    'adopt_model',
    'model_defaults',
    'save_model_default',
    'switch_workspace',
    'new_session',
    'open_session',
]

"""The facade methods whose body is a delegation to the session runtime."""

DELEGATED_PROPERTIES = ['session', 'busy', 'pending_question']

"""The facade properties that read the session runtime's execution state."""


def _tool_start(tool_name: str = 'shell') -> ToolCallEvent:
    """A tool event the session's sink and any other sink will both see."""
    return ToolCallEvent(tool_name=tool_name, phase='start')


def _retry_notice() -> RetryNotice:
    """One transient failure, as the retry capability hands it to its notifier."""
    return RetryNotice(attempt=1, next_attempt=2, max_attempts=3, delay=0.0, reason='transient')


def _mark(hub: EventHub) -> int:
    """The sequence number to read the hub after: everything before it is history."""
    events = hub.replay()
    return events[-1].seq if events else 0


def _types_after(hub: EventHub, mark: int) -> list[str]:
    """The event types published since ``mark``, in order."""
    return [event.type for event in hub.replay(after_seq=mark)]


async def _drop_task(owner: SessionRuntime) -> None:
    """Removes the stand-in turn task a test put on the owner."""
    task = owner._task
    owner._task = None
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _paths(tmp_path: Path):
    from src.core.providers.bootstrap import ProviderPaths

    home = tmp_path / '.sigil'
    home.mkdir(exist_ok=True)
    return ProviderPaths(home)


@pytest.fixture
def composition(tmp_path: Path) -> Composition:
    """A real composition with a stub model and its storage in a temporary directory."""
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        # `call_tools=[]` for the same reason `test_server_runtime.py` does it: the
        # default `TestModel` calls every tool, and `ask_user` would wait for an
        # answer no test is going to give.
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )
    # The guarded append-only path needs a known window; TestModel has no catalogue entry.
    composition.meter.set_window(100_000)
    return composition


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    return ServerRuntime(composition, EventHub(), require_ownership=False)


# --- the contract -----------------------------------------------------------


def test_should_expose_one_distinct_session_runtime_when_the_facade_is_built(
    runtime: ServerRuntime, composition: Composition
) -> None:
    """One facade, one session owner: the split is inspectable, not pretended."""
    assert isinstance(runtime.session_runtime, SessionRuntime)
    assert runtime.session_runtime.composition is composition
    assert runtime.session_runtime.hub is runtime.hub


def test_should_keep_the_same_session_runtime_between_reads(runtime: ServerRuntime) -> None:
    """The owner is created once. A property that built a new one would lose the queue."""
    assert runtime.session_runtime is runtime.session_runtime


def test_should_refuse_a_write_to_the_session_runtime_property() -> None:
    """Read-only: composition stays the only way to replace what a session drives."""
    assert isinstance(ServerRuntime.session_runtime, property)
    assert ServerRuntime.session_runtime.fset is None


@pytest.mark.parametrize('name', DELEGATED_METHODS)
def test_should_keep_the_public_method_signature(name: str) -> None:
    """A caller keeps calling the facade; the signature it sees is the one it had."""
    assert hasattr(ServerRuntime, name)
    assert hasattr(SessionRuntime, name)
    assert inspect.signature(getattr(ServerRuntime, name)) == inspect.signature(getattr(SessionRuntime, name))


@pytest.mark.parametrize('name', DELEGATED_PROPERTIES)
def test_should_keep_the_public_property_on_both_owners(name: str) -> None:
    """`session`, `busy` and `pending_question` are read from the execution owner."""
    assert isinstance(getattr(ServerRuntime, name), property)
    assert isinstance(getattr(SessionRuntime, name), property)


def test_should_keep_the_constructor_signature(runtime: ServerRuntime) -> None:
    """The existing arguments stay the existing arguments, keyword-only included."""
    parameters = inspect.signature(ServerRuntime).parameters
    assert list(parameters) == ['composition', 'hub', 'require_ownership', 'ownership', 'question_timeout']
    assert parameters['require_ownership'].default is True
    assert parameters['ownership'].default is None
    assert parameters['question_timeout'].default is None


def test_should_keep_the_support_types_importable_from_the_runtime_module() -> None:
    """Existing importers of `src.server.server_runtime` keep working unchanged."""
    from src.core.agents.tree.reports import WAKE_PROMPT
    from src.server.idle_shutdown import IdleShutdown
    from src.server.incoming_image import IncomingImage
    from src.server.pending_question import PendingQuestion
    from src.server.runtime_support import (
        IDLE_POLL_INTERVAL,
        PREVIEW_CHARS,
        QUESTION_TIMEOUT_ENV,
        decode_images,
        resolve_question_timeout,
    )
    from src.server.server_runtime import CHECKPOINT_INTERVAL, DEFAULT_IDLE_GRACE, QUESTION_TIMEOUT_SECONDS

    assert CHECKPOINT_INTERVAL > 0
    assert DEFAULT_IDLE_GRACE is None
    assert IDLE_POLL_INTERVAL > 0
    assert PREVIEW_CHARS > 0
    assert QUESTION_TIMEOUT_SECONDS is None
    assert QUESTION_TIMEOUT_ENV.startswith('SIGIL_')
    assert WAKE_PROMPT
    assert IncomingImage(media_type='image/png', data='').media_type == 'image/png'
    assert callable(decode_images)
    assert callable(resolve_question_timeout)
    assert isinstance(IdleShutdown(lambda: False, grace=0.0), IdleShutdown)

    async def pending() -> str:
        # The future is built from the running loop, which is the dataclass's own
        # rule; the import and the shape are what this test is about.
        return PendingQuestion(id='q1', questions=[]).id

    assert asyncio.run(pending()) == 'q1'


def test_should_not_import_the_facade_from_the_session_module() -> None:
    """The dependency runs one way: facade -> session -> support, never back."""
    tree = ast.parse(Path(session_runtime_module.__file__).read_text(encoding='utf-8'))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or '')
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert 'src.server.server_runtime' not in imported
    assert 'runtime' not in imported


def test_should_delegate_the_session_and_busy_facts(runtime: ServerRuntime) -> None:
    """`session` and `busy` answer with the session owner's state, not a copy of it."""
    assert runtime.session is runtime.session_runtime.session
    assert runtime.busy is False

    async def busy() -> tuple[bool, bool]:
        pending = asyncio.Event()
        runtime.session_runtime._task = asyncio.create_task(pending.wait())
        try:
            return runtime.busy, runtime.session_runtime.busy
        finally:
            task = runtime.session_runtime._task
            assert task is not None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            runtime.session_runtime._task = None

    assert asyncio.run(busy()) == (True, True)
    assert runtime.busy is False
    assert runtime.pending_question is None


# --- the lifecycle ----------------------------------------------------------


def test_should_detach_every_session_callback_when_the_session_closes(
    composition: Composition, runtime: ServerRuntime
) -> None:
    """All seven registrations `__init__` makes are undone by `close`.

    The two that close already detached — the question asker and this session's
    tool sink — are asserted too: a teardown that grew must not drop them. Each
    slot is proven active first, so nothing here passes because a callback never
    worked. See `.pas/state/active/00006-multi-session-service/callback-lifecycle.feature`.
    """
    owner = runtime.session_runtime

    async def exercise() -> None:
        # --- active before the close ---------------------------------------
        mark = _mark(runtime.hub)
        assert composition.extension_notice is not None
        composition.extension_notice('boom')
        assert composition.question_hub.available is True
        await composition.tool_logger.emit(_tool_start())
        composition.model_retry._notify(_retry_notice())
        composition.summoner._wake()
        assert composition.tree_changed is not None
        composition.tree_changed(SimpleNamespace(id='node-1'))
        assert _types_after(runtime.hub, mark) == [
            'notice',
            'tool_call',
            'notice',
            'state_changed',
            'tree_changed',
        ], 'every registration speaks before the close'

        # A turn is this session's fact while it owns the model service.
        pending = asyncio.Event()
        owner._task = asyncio.create_task(pending.wait())
        try:
            assert composition.model_service.is_busy() is True
        finally:
            await _drop_task(owner)

        # --- closed: every registration is gone or neutral ------------------
        owner.close()
        owner.close()  # closing twice is harmless

        assert composition.extension_notice is None
        assert composition.question_hub.available is False
        assert composition.model_retry.on_retry is None
        assert composition.tree_changed is None
        assert composition.summoner._on_idle is None

        # The busy predicate is neutral and no longer this session's flag: a task
        # left on a closed owner must not keep the service refusing model swaps.
        owner._task = asyncio.create_task(pending.wait())
        try:
            assert composition.model_service.is_busy() is False
        finally:
            await _drop_task(owner)

        # Nothing this session registered speaks after the close.
        mark = _mark(runtime.hub)
        await composition.tool_logger.emit(_tool_start('after'))
        composition.model_retry._notify(_retry_notice())
        composition.summoner._wake()
        assert _types_after(runtime.hub, mark) == []

        # `on_idle(None)` kept the composition's own tree relay registered: the
        # engine still announces changes to whoever attaches next.
        relayed: list[str] = []

        def relay(node: object) -> None:
            relayed.append(getattr(node, 'id', ''))

        composition.tree_changed = relay
        composition.summoner._on_change(SimpleNamespace(id='relay'))
        assert relayed == ['relay']

    asyncio.run(exercise())


def test_should_keep_the_process_lock_and_the_database_when_a_session_closes(
    composition: Composition, tmp_path: Path
) -> None:
    """A session owns execution; the process owns the store. Closing one frees nothing.

    The callbacks are the exception it does own: a second close must find them
    already detached, and a sink belonging to another owner must survive both.
    """
    runtime = ServerRuntime(composition, EventHub())
    runtime.start()
    path = composition.store.database.path
    other_owner: list[ToolCallEvent] = []
    composition.tool_logger.add_sink(other_owner.append)

    runtime.session_runtime.close()
    runtime.session_runtime.close()

    rival = ServerOwnership(path)
    with pytest.raises(OwnershipError):
        rival.acquire()
    assert composition.store.database.one('SELECT 1 AS one') is not None, 'the cached database is still open'
    assert AcceptedInputs(composition.store.database).has_open() is False
    assert composition.extension_notice is None
    assert composition.model_retry.on_retry is None
    assert composition.tree_changed is None
    assert composition.model_service.is_busy() is False

    asyncio.run(composition.tool_logger.emit(_tool_start('kept')))
    assert [event.tool_name for event in other_owner] == ['kept'], "another owner's sink survives"
    assert [event.type for event in runtime.hub.replay() if event.type == 'tool_call'] == []

    runtime.close()
    rival.acquire()
    rival.release()


def test_should_keep_a_never_executed_row_when_the_facade_closes_twice(
    composition: Composition,
) -> None:
    """Leaving on purpose never claims that unexecuted work was lost.

    Interruption is crash reconciliation and belongs to `start`. A graceful stop
    that marked these rows `interrupted` would be a lie the next run would act on.
    """
    runtime = ServerRuntime(composition, EventHub())
    store = composition.store
    session = store.create_session()
    composition.bind_session(session)
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'nobody ran me', [], 'turn')

    async def go() -> None:
        asked = asyncio.create_task(
            composition.question_hub.ask(
                [QuestionItem(question='¿Sigo?', options=[QuestionOption(label='si'), QuestionOption(label='no')])]
            )
        )
        while runtime.pending_question is None:
            await asyncio.sleep(0)  # a turn of the loop, not a wait
        runtime.close()
        runtime.close()
        assert await asyncio.wait_for(asked, 5) is None, 'the open question was released with nothing'
        assert runtime.pending_question is None
        assert composition.question_hub.available is False, 'the asker was detached'

    asyncio.run(go())

    rows = AcceptedInputs(store.database).list(session.id)
    assert [row.state for row in rows] == ['accepted'], 'shutdown is not reconciliation'
    assert [event.type for event in runtime.hub.replay() if event.type == 'question_closed'] == ['question_closed']
    path = store.database.path
    successor = ServerOwnership(path)
    successor.acquire()
    successor.release()


# --- the turn still belongs to the session ----------------------------------


def test_should_run_a_turn_against_the_session_state(runtime: ServerRuntime) -> None:
    """The consumer, the run and the checkpoint live on the session owner now."""

    async def go() -> list[str]:
        await runtime.send('hola')
        await asyncio.wait_for(runtime.wait(), 5)
        return [event.type for event in runtime.hub.replay()]

    types = asyncio.run(go())

    assert 'turn_started' in types
    assert 'turn_finished' in types
    assert runtime.session.messages, 'the turn was saved'
    assert runtime.session_runtime._run is None
    assert runtime.session_runtime._turn_session is None
    assert runtime.session_runtime._pending == {}
    assert runtime.session_runtime._attempts == {}


def test_session_shutdown_waits_for_mcp_loader_before_closing_gateway(
    runtime: ServerRuntime, composition: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()
    installed: list[object] = []
    closed: list[object] = []
    manager = object()

    def loader() -> object:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError('test loader was not released')
        return manager

    async def close_gateway(gateway: object) -> None:
        closed.extend(installed)

    monkeypatch.setattr(composition, 'mcp_loader', lambda: loader)
    monkeypatch.setattr(type(composition.mcp), 'install', lambda gateway, loaded: installed.append(loaded))
    monkeypatch.setattr(type(composition.mcp), 'aclose', close_gateway)

    async def run() -> None:
        owner = runtime.session_runtime
        owner._start_mcp()
        try:
            assert await asyncio.to_thread(started.wait, 2)
            shutdown = asyncio.create_task(owner.aclose())
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert closed == []
            release.set()
            await asyncio.wait_for(shutdown, timeout=2)
            assert closed == [manager]
        finally:
            release.set()

    asyncio.run(run())


def test_session_shutdown_waits_for_subagent_cleanup(runtime: ServerRuntime, composition: Composition) -> None:
    async def run() -> None:
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def child() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        task = asyncio.create_task(child())
        composition.summoner._tasks['test-child'] = task
        await started.wait()
        composition.summoner.cancel_all()
        shutdown = asyncio.create_task(runtime.session_runtime.aclose())
        try:
            await asyncio.wait_for(cleaning.wait(), timeout=2)
            assert not shutdown.done()
        finally:
            release.set()
            await asyncio.wait_for(shutdown, timeout=2)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert task.done()

    asyncio.run(run())


def test_session_shutdown_closes_mcp_gateway_when_install_fails(
    runtime: ServerRuntime, composition: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    closed: list[bool] = []

    def fail_install(gateway: object, manager: object) -> None:
        raise RuntimeError('install failed')

    async def close_gateway(gateway: object) -> None:
        closed.append(True)

    monkeypatch.setattr(composition, 'mcp_loader', lambda: lambda: SimpleNamespace(aclose=AsyncMock()))
    monkeypatch.setattr(type(composition.mcp), 'install', fail_install)
    monkeypatch.setattr(type(composition.mcp), 'aclose', close_gateway)

    async def run() -> None:
        owner = runtime.session_runtime
        owner._start_mcp()
        with pytest.raises(RuntimeError, match='install failed'):
            await owner.aclose()
        assert closed == [True]

    asyncio.run(run())


def test_cancelled_shutdown_waits_for_mcp_loader_before_closing(
    runtime: ServerRuntime, composition: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()
    closed: list[bool] = []

    def loader() -> object:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError('test loader was not released')
        return object()

    async def close_gateway(gateway: object) -> None:
        closed.append(True)

    monkeypatch.setattr(composition, 'mcp_loader', lambda: loader)
    monkeypatch.setattr(type(composition.mcp), 'install', lambda gateway, loaded: None)
    monkeypatch.setattr(type(composition.mcp), 'aclose', close_gateway)

    async def run() -> None:
        owner = runtime.session_runtime
        owner._start_mcp()
        try:
            assert await asyncio.to_thread(started.wait, 2)
            shutdown = asyncio.create_task(owner.aclose())
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert closed == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(shutdown, timeout=2)
            assert closed == [True]
        finally:
            release.set()

    asyncio.run(run())


def test_registry_keeps_distinct_session_compositions(
    runtime: ServerRuntime, composition: Composition, tmp_path: Path
) -> None:
    other_root = tmp_path / 'other-workspace'
    other_root.mkdir()
    other_session = composition.store.draft_session()
    other_session.cwd = str(other_root)

    first = runtime.resident_session(composition.session)
    second = runtime.resident_session(other_session)

    assert first is runtime.session_runtime
    assert runtime.resident_session(other_session) is second
    assert second is not first
    assert second.composition is not composition
    assert second.session is other_session
    assert second.composition.root == other_root
    assert second.composition.settings is composition.settings
    assert second.composition.provider_paths == composition.provider_paths
    assert second.composition._model_override is composition._model_override
    assert second.composition.store.database is composition.store.database
    assert runtime.composition is composition
    assert composition.session._draft


def test_service_shutdown_closes_all_resident_sessions_before_releasing_lock(
    composition: Composition, tmp_path: Path
) -> None:
    runtime = ServerRuntime(composition, EventHub())
    runtime.start()
    second = runtime.resident_session(composition.store.draft_session())
    rival = ServerOwnership(composition.store.database.path)

    async def run() -> None:
        await runtime.close_sessions()
        assert runtime.session_runtime._closing
        assert second._closing
        with pytest.raises(OwnershipError):
            rival.acquire()
        assert composition.store.database.query('SELECT 1')[0][0] == 1
        runtime.close()
        rival.acquire()
        rival.release()

    asyncio.run(run())


def test_resident_session_events_are_visible_only_to_their_owner(
    runtime: ServerRuntime, composition: Composition
) -> None:
    first = runtime.session_runtime
    second = runtime.resident_session(composition.store.draft_session())
    marker = runtime.hub.last_seq

    first.composition.extension_notice('first only')
    second.composition.extension_notice('second only')
    runtime.hub.publish('service_notice')

    first_events = runtime.hub.replay(after_seq=marker, session_id=first.session.id)
    second_events = runtime.hub.replay(after_seq=marker, session_id=second.session.id)
    assert [event.data.get('text') for event in first_events] == ['first only', None]
    assert [event.data.get('text') for event in second_events] == ['second only', None]


def test_event_stream_filters_replay_and_live_events(runtime: ServerRuntime, composition: Composition) -> None:
    from src.server.app import _stream

    first = runtime.session_runtime
    second = runtime.resident_session(composition.store.draft_session())

    async def disconnected() -> bool:
        return False

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(stopping=False)), is_disconnected=disconnected)
    hub = runtime.hub
    hub.publish('first_replay', owner_session_id=first.session.id)
    hub.publish('second_replay', owner_session_id=second.session.id)
    hub.publish('global_replay')

    async def run() -> None:
        stream = _stream(hub, request, 0, session_id=first.session.id)
        try:
            assert b'first_replay' in await anext(stream)
            assert b'global_replay' in await anext(stream)
            hub.publish('second_live', owner_session_id=second.session.id)
            hub.publish('first_live', owner_session_id=first.session.id)
            assert b'first_live' in await asyncio.wait_for(anext(stream), timeout=2)
        finally:
            await stream.aclose()

    asyncio.run(run())


def test_service_idle_check_sees_work_in_a_secondary_session(runtime: ServerRuntime, composition: Composition) -> None:
    second = runtime.resident_session(composition.store.draft_session())

    async def run() -> None:
        second._task = asyncio.create_task(asyncio.Event().wait())
        try:
            assert runtime.has_active_work()
        finally:
            await _drop_task(second)

    asyncio.run(run())


def test_consumer_failure_interrupts_only_its_own_session(
    runtime: ServerRuntime, composition: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = runtime.session_runtime
    second = runtime.resident_session(composition.store.draft_session())
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    composition.store.accept_input(first.session, first_id, 'first', [], 'turn')
    second.composition.store.accept_input(second.session, second_id, 'second', [], 'turn')

    def fail_claim() -> None:
        raise RuntimeError('consumer failed')

    monkeypatch.setattr(first, '_claim_next', fail_claim)
    asyncio.run(first._consume())
    queue = AcceptedInputs(composition.store.database)
    assert queue.list(first.session.id)[0].state == 'interrupted'
    assert queue.list(second.session.id)[0].state == 'accepted'


def test_registry_keeps_the_same_owner_across_legacy_selection_changes(runtime: ServerRuntime) -> None:
    first = runtime.session_runtime
    second = runtime.resident_session(runtime.composition.store.draft_session())
    runtime._sessions[first.session.id] = first
    # Persisting an empty draft is deliberately unnecessary for a resident open.
    runtime.open_session(second.session.id)
    assert runtime.session_runtime is second
    assert runtime.find_session(first.session.id) is first
    runtime.open_session(first.session.id)
    assert runtime.session_runtime is first
    assert runtime.find_session(second.session.id) is second


def test_two_resident_sessions_run_turns_concurrently_without_queue_crosstalk(runtime: ServerRuntime) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel

    first = runtime.session_runtime
    second = runtime.resident_session(runtime.composition.store.draft_session())

    async def run() -> None:
        arrived = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        def model(index: int) -> Agent:
            async def answer(messages: Any, info: Any) -> Any:
                arrived[index].set()
                await release.wait()
                yield f'answer-{index}'

            return Agent(FunctionModel(stream_function=answer))

        first.composition.agent = model(0)
        second.composition.agent = model(1)
        try:
            await first.send('first question', str(uuid.uuid4()))
            await second.send('second question', str(uuid.uuid4()))
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in arrived)), timeout=3)
            assert first.busy and second.busy
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(first.wait(), second.wait()), timeout=5)
            await runtime.close_sessions()
        first_queue = runtime._accepted.list(first.session.id)
        second_queue = runtime._accepted.list(second.session.id)
        assert len(first_queue) == len(second_queue) == 1
        assert first_queue[0].text == 'first question'
        assert second_queue[0].text == 'second question'
        assert first_queue[0].state == second_queue[0].state == 'finished'

    asyncio.run(run())


def test_idle_release_respects_clients_work_and_shared_ownership(runtime: ServerRuntime) -> None:
    first = runtime.session_runtime
    second = runtime.resident_session(runtime.composition.store.draft_session())
    runtime.touch_session(first.session.id, now=0)
    runtime.touch_session(second.session.id, now=0)

    async def run() -> None:
        with runtime.session_lease(first.session.id):
            await runtime.release_idle_sessions(grace=10, now=10)
            assert runtime.find_session(first.session.id) is first
            assert runtime.find_session(second.session.id) is None
        assert runtime.composition.store.database.one('SELECT 1') is not None
        assert first._closing is False
        await runtime.close_sessions()

    asyncio.run(run())


@pytest.mark.parametrize('activity', ['turn', 'queue', 'subagent', 'report'])
def test_idle_release_keeps_session_work(
    runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch, activity: str
) -> None:
    owner = runtime.session_runtime
    runtime.touch_session(owner.session.id, now=0)

    async def run() -> None:
        if activity == 'turn':
            owner._task = asyncio.create_task(asyncio.Event().wait())
        elif activity == 'queue':
            owner.composition.store.accept_input(owner.session, str(uuid.uuid4()), 'queued', [], 'turn')
        elif activity == 'subagent':
            monkeypatch.setattr(owner.composition.summoner, 'running', lambda: 1)
        else:
            monkeypatch.setattr(owner.composition.summoner, 'waiting', lambda: ['report'])
        try:
            await runtime.release_idle_sessions(grace=10, now=100)
            assert runtime.find_session(owner.session.id) is owner
            assert owner._closing is False
        finally:
            await _drop_task(owner)
            await runtime.close_sessions()

    asyncio.run(run())


def test_switched_away_session_reads_its_own_finished_subagent_report(runtime: ServerRuntime) -> None:
    from src.core.agents.profile.models.agent_role import AgentRole

    first = runtime.session_runtime
    second = runtime.resident_session(runtime.composition.store.draft_session())
    runtime._select_default(second)
    tree = first.composition.agent_trees.active
    tree.ensure_root('wizard')
    node = tree.add(parent_id=tree.root_id, name='researcher', role=AgentRole.SUBAGENT, task='read it')
    node.mode = 'async'
    node.finish('report-for-the-original-session')
    node.delivered = False

    async def run() -> None:
        try:
            first.ensure_workers()
            first._on_tree_idle()
            await asyncio.wait_for(first.wait(), timeout=3)
            assert node.delivered is True
            assert len(runtime._accepted.list(first.session.id)) == 1
            assert runtime._accepted.list(second.session.id) == []
            assert second.session.messages == []
            assert runtime.load_resident(first.session.id) is first
        finally:
            await runtime.close_sessions()

    asyncio.run(run())


def test_concurrent_open_returns_one_resident_owner(runtime: ServerRuntime) -> None:
    session = runtime.composition.store.create_session()

    async def run() -> None:
        try:
            owners = await asyncio.gather(*(runtime.open_resident(session.id) for _ in range(4)))
            assert all(owner is owners[0] for owner in owners)
            assert runtime.find_session(session.id) is owners[0]
        finally:
            await runtime.close_sessions()

    asyncio.run(run())


def test_failed_mcp_install_closes_the_manager_it_could_not_adopt(
    runtime: ServerRuntime, composition: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    manager = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(composition, 'mcp_loader', lambda: lambda: manager)

    def fail_install(gateway: object, loaded: object) -> None:
        raise RuntimeError('cannot install')

    monkeypatch.setattr(type(composition.mcp), 'install', fail_install)

    async def run() -> None:
        runtime.ensure_workers()
        with pytest.raises(RuntimeError, match='cannot install'):
            await runtime.session_runtime.aclose()
        manager.aclose.assert_awaited_once()

    asyncio.run(run())


def test_stopping_one_session_leaves_another_turn_running(runtime: ServerRuntime) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel

    first = runtime.session_runtime
    second = runtime.resident_session(runtime.composition.store.draft_session())

    async def run() -> None:
        started = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        def agent(index: int) -> Agent:
            async def answer(messages: Any, info: Any) -> Any:
                started[index].set()
                await release.wait()
                yield 'finished'

            return Agent(FunctionModel(stream_function=answer))

        first.composition.agent, second.composition.agent = agent(0), agent(1)
        try:
            await first.send('first')
            await second.send('second')
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=3)
            first.stop_all()
            await asyncio.wait_for(first.wait(), timeout=3)
            assert not first.busy
            assert second.busy
            release.set()
            await asyncio.wait_for(second.wait(), timeout=3)
            assert runtime._accepted.list(first.session.id)[0].state == 'stopped'
            assert runtime._accepted.list(second.session.id)[0].state == 'finished'
        finally:
            release.set()
            await runtime.close_sessions()

    asyncio.run(run())


@pytest.mark.parametrize('shutting_down', [False, True])
def test_cancelled_open_keeps_registry_ownership_until_factory_finishes(
    runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch, shutting_down: bool
) -> None:
    import threading

    import src.server.server_runtime as runtime_module

    session = runtime.composition.store.create_session()
    started = threading.Event()
    release = threading.Event()
    original = runtime_module.Composition
    made: list[Composition] = []

    def compose(*args: Any, **kwargs: Any) -> Composition:
        started.set()
        assert release.wait(timeout=3), 'the test must release the constructor'
        composition = original(*args, **kwargs)
        made.append(composition)
        return composition

    monkeypatch.setattr(runtime_module, 'Composition', compose)

    async def run() -> None:
        opening = asyncio.create_task(runtime.open_resident(session.id))
        assert await asyncio.to_thread(started.wait, 2)
        opening.cancel()
        followup = asyncio.create_task(runtime.close_sessions() if shutting_down else runtime.open_resident(session.id))
        try:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(opening, timeout=3)
            result = await asyncio.wait_for(followup, timeout=3)
            assert len(made) == 1
            owner = runtime.find_session(session.id)
            assert owner is not None and owner.composition is made[0]
            if shutting_down:
                assert owner._closing is True
                assert owner._consumer is None or owner._consumer.done()
            else:
                assert result is owner
        finally:
            release.set()
            await runtime.close_sessions()

    asyncio.run(run())


def test_session_shutdown_cancels_and_awaits_its_background_title_task(
    runtime: ServerRuntime, composition: Composition, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def title(model: Any, text: str) -> str:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
            return 'unused'

        monkeypatch.setattr('src.core.sessions.titling.generate_title', title)
        monkeypatch.setattr(composition.model_service, 'current_model', lambda: composition._model_override)
        owner = runtime.session_runtime
        owner._name_session(owner.session, 'hello')
        await asyncio.wait_for(started.wait(), timeout=2)
        await owner.aclose()
        assert cleaned.is_set()

    asyncio.run(run())
