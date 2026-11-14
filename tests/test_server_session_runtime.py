"""The service facade and the one session runtime it owns.

Two boundaries live here, each proven at the cheapest level that can prove it:

- **The contract** (`@contract`): `ServerRuntime` keeps its imports, its
  constructor and its public method signatures, and the execution state it
  delegates belongs to a distinct, stable `SessionRuntime`.
- **The lifecycle** (`@integration`): closing a session cannot release the
  process lock or close the shared cached database, and closing the facade
  releases that lock once without interrupting rows it never executed. A real
  temporary database and a real `flock` are the contract here; a mock of either
  would prove nothing about a lifetime.

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

import pytest
from pydantic_ai.models.test import TestModel

from src.engine.bootstrap import Composition
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_option import QuestionOption
from src.engine.settings import load_settings
from src.engine.storage.accepted_inputs import AcceptedInputs
from src.server import session_runtime as session_runtime_module
from src.server.events import EventHub
from src.server.ownership import OwnershipError, ServerOwnership
from src.server.runtime import ServerRuntime
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


def _paths(tmp_path: Path):
    from src.engine.providers.bootstrap import ProviderPaths

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
    """Existing importers of `src.server.runtime` keep working unchanged."""
    from src.server.runtime import (
        CHECKPOINT_INTERVAL,
        DEFAULT_IDLE_GRACE,
        IDLE_POLL_INTERVAL,
        PREVIEW_CHARS,
        QUESTION_TIMEOUT_ENV,
        QUESTION_TIMEOUT_SECONDS,
        WAKE_PROMPT,
        IdleShutdown,
        IncomingImage,
        PendingQuestion,
        decode_images,
        resolve_question_timeout,
    )

    assert CHECKPOINT_INTERVAL > 0
    assert DEFAULT_IDLE_GRACE > 0
    assert IDLE_POLL_INTERVAL > 0
    assert PREVIEW_CHARS > 0
    assert QUESTION_TIMEOUT_SECONDS > 0
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
    assert 'src.server.runtime' not in imported
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


def test_should_keep_the_process_lock_and_the_database_when_a_session_closes(
    composition: Composition, tmp_path: Path
) -> None:
    """A session owns execution; the process owns the store. Closing one frees nothing."""
    runtime = ServerRuntime(composition, EventHub())
    runtime.start()
    path = composition.store.database.path

    runtime.session_runtime.close()

    rival = ServerOwnership(path)
    with pytest.raises(OwnershipError):
        rival.acquire()
    assert composition.store.database.one('SELECT 1 AS one') is not None, 'the cached database is still open'
    assert AcceptedInputs(composition.store.database).has_open() is False

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
