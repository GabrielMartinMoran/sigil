"""The headless turn loop: the same decisions as the terminal, without widgets.

The engine's turn machinery is tested against `TestModel` in `test_steering_and_abort`,
so this file tests what the *server* adds around it: that a turn publishes the events a
browser needs, that the session is saved, that abort and steering are wired, and that
the routes answer with the projections they claim.

`TestModel` is used instead of a network model for the obvious reason, and because the
events a real model produces are a subset of what this exercises: the deltas come from
the same `ProcessEventStream`.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import subprocess
import sys
import threading
import uuid
import zlib
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from starlette.types import Message, Receive, Scope, Send

from src.engine.agents.model_config import AgentModelConfig
from src.engine.agents.model_defaults import ConfigScope
from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.models.summon_error import SummonError
from src.engine.bootstrap import Composition
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_option import QuestionOption
from src.engine.settings import load_settings
from src.server import views
from src.server.app import BodyLimitMiddleware, create_app
from src.server.config import ServerConfig
from src.server.events import EventHub
from src.server.ownership import OwnershipError, ServerOwnership
from src.server.runtime import (
    QUESTION_TIMEOUT_ENV,
    QUESTION_TIMEOUT_SECONDS,
    WAKE_PROMPT,
    PendingQuestion,
    ServerRuntime,
    resolve_question_timeout,
)
from src.ui.subagent_reports import WAKE_PROMPT as TERMINAL_WAKE_PROMPT


@pytest.fixture
def composition(tmp_path: Path) -> Composition:
    """A real composition with a stub model and its storage in a temporary directory."""
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        # `call_tools=[]` on purpose: `TestModel` calls every tool by default, and one
        # of them is `ask_user`, which waits for an answer no test is going to give.
        # The turn would block on a question instead of finishing, and the test would
        # be measuring the wrong thing.
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )
    # The guarded append-only path needs a known window; TestModel has no catalogue entry.
    composition.meter.set_window(100_000)
    return composition


def _paths(tmp_path: Path):
    from src.engine.providers.bootstrap import ProviderPaths

    home = tmp_path / '.sigil'
    home.mkdir(exist_ok=True)
    return ProviderPaths(home)


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    return ServerRuntime(composition, EventHub(), require_ownership=False)


def test_a_turn_publishes_start_deltas_and_finish(runtime: ServerRuntime) -> None:
    """The event sequence a client depends on: the deltas are the typing."""

    async def go() -> list[str]:
        await runtime.send('hola')
        await runtime.wait()
        return [event.type for event in runtime.hub.replay()]

    types = asyncio.run(go())

    # No order is promised between event types: `_start_mcp` publishes
    # `state_changed(reason='mcp')` from a background task, so it can land before the
    # turn starts and the turn still be correct. What a client depends on is that the
    # turn starts and that the deltas come after that, not that nothing precedes it.
    assert 'turn_started' in types

    started = types.index('turn_started')
    assert 'text_delta' in types[started:], types
    assert 'turn_finished' in types[started:], types


def test_a_turn_is_saved_in_the_session(runtime: ServerRuntime) -> None:
    """A turn that is not persisted is a turn the next request cannot continue."""

    async def go() -> int:
        await runtime.send('hola')
        await runtime.wait()
        return len(runtime.session.messages)

    assert asyncio.run(go()) > 0
    assert runtime.composition.store.load(runtime.session.id) is not None


def test_an_empty_message_is_refused(runtime: ServerRuntime) -> None:
    async def go() -> None:
        with pytest.raises(ValueError, match='empty'):
            await runtime.send('   ')

    asyncio.run(go())


def test_sending_while_busy_steers_instead_of_starting_a_second_turn(runtime: ServerRuntime) -> None:
    """Two turns at once would interleave their histories into an invalid conversation."""

    async def go() -> str:
        await runtime.send('primero')
        # The turn is scheduled but may not have started; either way it is accepted as
        # a steer or as a turn, and never as a second concurrent turn.
        result = await runtime.send('segundo')
        await runtime.wait()
        return str(result['accepted'])

    assert asyncio.run(go()) in {'steer', 'turn'}


def test_abort_when_idle_is_a_no_op(runtime: ServerRuntime) -> None:
    assert runtime.abort() is False


def test_the_state_projection_has_every_section_the_client_reads(runtime: ServerRuntime) -> None:
    from src.server.app import _state

    state = asyncio.run(_state(runtime))

    for key in ('session', 'workspace', 'model', 'agent', 'agents', 'tree', 'roadmap', 'busy', 'question', 'mcp'):
        assert key in state, key
    assert state['session']['id'] == runtime.session.id
    assert state['busy'] is False


def test_a_question_is_published_and_answered(runtime: ServerRuntime) -> None:
    """The `ask_user` tool blocks on an answer that arrives over HTTP."""
    from src.engine.interaction.models.question_item import QuestionItem
    from src.engine.interaction.models.question_option import QuestionOption

    item = QuestionItem(
        question='¿Sigo?',
        options=[QuestionOption(label='si'), QuestionOption(label='no')],
    )

    async def go() -> list[str]:
        task = asyncio.create_task(runtime.composition.question_hub.ask([item]))
        await asyncio.sleep(0)
        pending = runtime.pending_question
        assert pending is not None
        assert runtime.answer(pending.id, [{'question': '¿Sigo?', 'answer': 'si'}])
        answers = await task
        assert answers is not None and answers[0].answer == 'si'
        return [event.type for event in runtime.hub.replay()]

    types = asyncio.run(go())
    assert 'question' in types
    assert 'question_closed' in types


def test_a_question_can_be_cancelled(runtime: ServerRuntime) -> None:
    from src.engine.interaction.models.question_item import QuestionItem
    from src.engine.interaction.models.question_option import QuestionOption

    item = QuestionItem(question='¿Sigo?', options=[QuestionOption(label='si'), QuestionOption(label='no')])

    async def go() -> None:
        task = asyncio.create_task(runtime.composition.question_hub.ask([item]))
        await asyncio.sleep(0)
        pending = runtime.pending_question
        assert pending is not None
        assert runtime.answer(pending.id, None)
        assert await task is None

    asyncio.run(go())


def test_answering_an_unknown_question_is_reported(runtime: ServerRuntime) -> None:
    assert runtime.answer('nope', []) is False


QUESTION_DEADLINE = 10.0
"""How long a test waits for something that has to end by itself, not how long it takes.

A turn released by the question deadline must finish on its own; this is the ceiling
that turns "it hangs" into a failure instead of a suite that never returns.
"""


async def _await_pending(runtime: ServerRuntime) -> PendingQuestion:
    """The question the runtime has just published, without a guessed sleep."""
    for _ in range(1_000):
        pending = runtime.pending_question
        if pending is not None:
            return pending
        await asyncio.sleep(0)
    raise AssertionError('the runtime never published its question')


def _question_pair() -> list[QuestionItem]:
    return [QuestionItem(question='¿Sigo?', options=[QuestionOption(label='si'), QuestionOption(label='no')])]


def test_the_question_deadline_comes_from_the_argument_or_the_environment() -> None:
    """The deadline is a named constant with two overrides, and a typo is not a default."""

    assert resolve_question_timeout(env={}) == QUESTION_TIMEOUT_SECONDS
    assert resolve_question_timeout(2.5) == 2.5
    assert resolve_question_timeout(env={QUESTION_TIMEOUT_ENV: '7'}) == 7.0
    with pytest.raises(ValueError, match=QUESTION_TIMEOUT_ENV):
        resolve_question_timeout(env={QUESTION_TIMEOUT_ENV: 'soon'})
    with pytest.raises(ValueError):
        resolve_question_timeout(0)


def test_a_question_nobody_answers_releases_the_turn(tmp_path: Path) -> None:
    """A question with no client is a deadline, not a wait that never ends.

    The turn is released with ``None`` — the value a cancellation already uses — so
    `ask_user` cancels the run and the turn ends where it stood. Nothing is invented
    on the user's behalf, and the events say which of the two it was.
    """

    async def go() -> None:
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            # `ask_user` is the tool under test, so this model calls it and waits.
            model=TestModel(custom_output_text='done', call_tools=['ask_user']),
            provider_paths=_paths(tmp_path),
        )
        composition.meter.set_window(100_000)
        # The deadline is injected: the test measures the release, not the wait.
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False, question_timeout=0.01)
        try:
            await runtime.send('hola')
            await asyncio.wait_for(runtime.wait(), timeout=QUESTION_DEADLINE)

            events = runtime.hub.replay()
            closed = [event for event in events if event.type == 'question_closed']
            notices = [str(event.data.get('text') or '') for event in events if event.type == 'notice']
            finished = [event for event in events if event.type == 'turn_finished']
            assert runtime.pending_question is None, 'an expired question stays published'
            assert closed and closed[-1].data.get('reason') == 'timeout', closed
            assert any('no answer' in text.lower() for text in notices), notices
            assert finished and finished[-1].data.get('status') == 'cancelled', finished
        finally:
            runtime.close()

    asyncio.run(go())


def test_closing_with_a_question_open_releases_it(composition: Composition) -> None:
    """A process that leaves with a question open must not leave a future behind it."""

    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)

    async def go() -> None:
        asked = asyncio.create_task(runtime.composition.question_hub.ask(_question_pair()))
        pending = await _await_pending(runtime)
        runtime.close()

        assert await asyncio.wait_for(asked, timeout=QUESTION_DEADLINE) is None
        assert pending.future.done(), 'the question was left waiting with no one to answer it'
        closed = [event for event in runtime.hub.replay() if event.type == 'question_closed']
        assert closed and closed[-1].data.get('reason') == 'closed', closed

    asyncio.run(go())


def test_two_questions_with_the_same_text_are_answered_apart(composition: Composition) -> None:
    """The id is what identifies a question: the same words twice are two questions."""

    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)

    async def ask_once(answer: str) -> str:
        asked = asyncio.create_task(runtime.composition.question_hub.ask(_question_pair()))
        pending = await _await_pending(runtime)
        assert runtime.answer(pending.id, [{'question': '¿Sigo?', 'answer': answer}])
        answers = await asyncio.wait_for(asked, timeout=QUESTION_DEADLINE)
        assert answers is not None and answers[0].answer == answer
        return pending.id

    async def go() -> tuple[str, str]:
        return await ask_once('si'), await ask_once('no')

    first, second = asyncio.run(go())
    assert first != second


def test_new_session_and_open_session_round_trip(runtime: ServerRuntime) -> None:
    first = runtime.session
    second = runtime.new_session()
    assert second.id != first.id
    assert runtime.session.id == second.id
    with pytest.raises(LookupError):
        runtime.open_session(first.id)
    assert runtime.composition.store.list_sessions() == []


def test_the_routes_answer_with_the_projections(tmp_path: Path, composition: Composition) -> None:
    """The HTTP surface, exercised through the app with a real runtime behind it."""
    from fastapi.testclient import TestClient

    from src.server.app import create_app

    runtime = ServerRuntime(composition, EventHub())
    config = ServerConfig(host='127.0.0.1', port=8760, password='secret-pass')
    client = TestClient(create_app(runtime, config, dist=tmp_path / 'missing'))

    assert client.get('/api/state').status_code == 401

    assert client.post('/api/login', json={'password': 'wrong'}).status_code == 401
    login = client.post('/api/login', json={'password': 'secret-pass'})
    assert login.status_code == 200
    assert 'sigil_session' in login.headers['set-cookie']

    state = client.get('/api/state')
    assert state.status_code == 200
    assert state.json()['session']['id'] == runtime.session.id

    assert client.get('/api/session/transcript').status_code == 200
    assert client.get('/api/sessions').status_code == 200
    assert client.get('/api/workspaces').status_code == 200

    created = client.post('/api/session/new')
    assert created.status_code == 200
    assert created.json()['session']['id'] == runtime.session.id


def test_the_bundle_missing_is_a_503_and_not_a_crash(tmp_path: Path, composition: Composition) -> None:
    """A server that starts without a bundle must say so, not fail to answer."""
    from fastapi.testclient import TestClient

    from src.server.app import create_app

    runtime = ServerRuntime(composition, EventHub())
    client = TestClient(create_app(runtime, ServerConfig(password='x'), dist=tmp_path / 'nope'))
    response = client.get('/')
    assert response.status_code == 503
    assert 'not built' in response.text


def _bundle(tmp_path: Path) -> Path:
    """A `dist` with the two things `serve` mounts: the page and one asset."""
    dist = tmp_path / 'dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text('<html>the web client</html>', encoding='utf-8')
    (dist / 'assets' / 'app.js').write_text('console.log("client")', encoding='utf-8')
    return dist


def test_the_api_backend_serves_no_client_and_no_login(tmp_path: Path, composition: Composition) -> None:
    """`sigil server` is the API alone: a bundle on disk changes nothing.

    The bundle is deliberately real and passed in, because the assertion is that it
    is *not* mounted. `/` answers with the sentence that names the command a browser
    visitor should run, instead of a 404 that says nothing.
    """
    from fastapi.testclient import TestClient

    from src.server.app import create_app
    from src.server.config import resolve_config

    runtime = ServerRuntime(composition, EventHub())
    config = resolve_config(host='127.0.0.1', port=8760, env={}, web=False)
    client = TestClient(create_app(runtime, config, dist=_bundle(tmp_path)))

    root = client.get('/')
    assert root.status_code == 200
    assert 'sigil serve' in root.text
    assert 'text/html' not in root.headers['content-type']

    assert client.get('/static/assets/app.js').status_code == 404
    assert client.get('/assets/app.js').status_code == 404
    assert client.post('/api/login', json={'password': 'anything'}).status_code == 404


def test_serve_mounts_the_client_and_asks_for_the_password(tmp_path: Path, composition: Composition) -> None:
    """`sigil serve` is the same process plus the page, and the page is behind a password."""
    from fastapi.testclient import TestClient

    from src.server.app import create_app
    from src.server.config import PASSWORD_ENV, resolve_config

    runtime = ServerRuntime(composition, EventHub())
    config = resolve_config(host='127.0.0.1', port=8760, env={PASSWORD_ENV: 'secret-pass'})
    client = TestClient(create_app(runtime, config, dist=_bundle(tmp_path)))

    assert config.web is True
    page = client.get('/')
    assert page.status_code == 200
    assert 'the web client' in page.text
    assert client.get('/static/app.js').status_code == 200

    assert client.get('/api/state').status_code == 401
    assert client.post('/api/login', json={'password': 'secret-pass'}).status_code == 200
    assert client.get('/api/state').status_code == 200


def test_the_backend_accepts_the_endpoint_token_for_a_turn(tmp_path: Path, runtime: ServerRuntime) -> None:
    """The API surface is still the whole API: the token opens `/api/turn`."""
    from src.server.app import create_app
    from src.server.config import resolve_config
    from src.server.endpoint import EndpointPublisher

    publisher = EndpointPublisher(path=tmp_path / 'service' / 'endpoint.json')
    app = create_app(
        runtime,
        resolve_config(host='127.0.0.1', port=8760, env={}, web=False),
        dist=tmp_path / 'missing',
        publisher=publisher,
    )
    bearer = {'Authorization': f'Bearer {publisher.token}'}

    async def go() -> tuple[int, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://sigil.test') as client:
            refused = await client.post('/api/turn', json={'text': 'hello'})
            accepted = await client.post('/api/turn', json={'text': 'hello'}, headers=bearer)
            await runtime.wait()
            return refused.status_code, accepted.status_code

    assert asyncio.run(go()) == (401, 200)


def test_logout_clears_the_cookie(tmp_path: Path, composition: Composition) -> None:
    from fastapi.testclient import TestClient

    from src.server.app import create_app

    runtime = ServerRuntime(composition, EventHub())
    client = TestClient(create_app(runtime, ServerConfig(password='secret-pass'), dist=tmp_path / 'nope'))
    client.post('/api/login', json={'password': 'secret-pass'})
    logout = client.post('/api/logout')
    assert 'Max-Age=0' in logout.headers['set-cookie']
    assert client.get('/api/state').status_code == 401


def test_unknown_context_window_is_reported_as_turn_error(runtime: ServerRuntime) -> None:
    runtime.composition.meter.set_window(None)

    async def go() -> None:
        await runtime.send('work')
        await runtime.wait()

    asyncio.run(go())
    finished = next(event for event in runtime.hub.replay() if event.type == 'turn_finished')
    assert finished.data['status'] == 'error'
    assert 'unknown' in finished.data['error']


def test_send_reloads_external_conversation_before_starting(runtime: ServerRuntime) -> None:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    store = runtime.composition.store
    runtime.session.messages = [ModelRequest(parts=[UserPromptPart('initial')])]
    store.save(runtime.session)
    external = store.load(runtime.session.id)
    external.messages = [
        ModelRequest(parts=[UserPromptPart('external question')], run_id='external'),
        ModelResponse(parts=[TextPart('external answer')], run_id='external'),
    ]
    store.save(external)

    async def go() -> None:
        await runtime.send('continue')
        await runtime.wait()

    asyncio.run(go())

    assert runtime.session.messages[:2] == external.messages
    assert any(event.type == 'turn_finished' for event in runtime.hub.replay())


def test_external_write_during_a_turn_preserves_both_histories(runtime: ServerRuntime) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    store = runtime.composition.store
    runtime.session.messages = [ModelRequest(parts=[UserPromptPart('initial')], run_id='initial')]
    store.save(runtime.session)
    original_id = runtime.session.id

    async def answer(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        external = store.load(original_id)
        external.messages = [
            ModelRequest(parts=[UserPromptPart('remote question')], run_id='remote'),
            ModelResponse(parts=[TextPart('remote answer')], run_id='remote'),
        ]
        store.save(external)
        yield 'local result'

    runtime.composition.agent = Agent(FunctionModel(stream_function=answer))

    async def go() -> None:
        await runtime.send('local question')
        await runtime.wait()

    asyncio.run(go())

    assert runtime.session.messages[0].parts[0].content == 'remote question'
    recovered = [item for item in store.list_sessions() if item.id != original_id]
    assert len(recovered) == 1
    backup = store.load(recovered[0].id)
    assert backup.messages[-1].parts[0].content == 'local result'
    assert any(event.type == 'turn_finished' for event in runtime.hub.replay())


def test_terminal_session_switch_updates_the_composition_owner(composition: Composition) -> None:
    from src.ui.app import SigilApp

    async def go() -> None:
        app = SigilApp(
            agent=composition.agent,
            store=composition.store,
            session=composition.session,
            roadmap_store=composition.roadmap_store,
            agent_tree=composition.agent_trees,
            on_bind_session=composition.bind_session,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            previous = composition.session
            await app.handle_command('/new')
            assert composition.session is app._session
            assert composition.session is not previous
            assert composition.agent_trees.active is app._session.tree

    asyncio.run(go())


def test_startup_does_not_resume_previous_session(composition: Composition, tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    previous = composition.session
    previous.messages = [ModelRequest(parts=[UserPromptPart('previous')])]
    composition.store.save(previous)
    reopened = Composition(
        tmp_path,
        settings=load_settings(),
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )
    assert reopened.session.id != previous.id
    assert reopened.session.messages == []
    assert reopened.store.load(reopened.session.id) is None
    assert reopened.store.load(previous.id) is not None


def test_switching_workspace_leaves_an_empty_draft(runtime: ServerRuntime, tmp_path: Path) -> None:
    """Coming back to a workspace starts empty instead of reopening its last conversation.

    The turn below is saved in this workspace, so the switch back used to find it and
    open it. That is the regression: the workspace switch chose the conversation for
    the user. Opening one is an explicit act (`/load`, `/api/session/open`).
    """

    async def go() -> None:
        await runtime.send('guardado')
        await runtime.wait()

    asyncio.run(go())
    saved = runtime.session
    assert saved.messages
    assert runtime.composition.store.load(saved.id) is not None

    other = tmp_path / 'other'
    other.mkdir()
    runtime.switch_workspace(str(other))
    runtime.switch_workspace(str(tmp_path))

    assert runtime.session.id != saved.id
    assert runtime.session.messages == []


def test_abort_cancels_only_the_runtime_composition(runtime: ServerRuntime) -> None:
    from src.engine.shell.running import RunningCommands

    other = RunningCommands()
    owner = runtime.composition.shell_commands
    own_generation, other_generation = owner.generation(), other.generation()

    async def check() -> None:
        pending = asyncio.Event()
        runtime._task = asyncio.create_task(pending.wait())
        try:
            assert runtime.abort()
            assert owner.generation() == own_generation + 1
            assert other.generation() == other_generation
        finally:
            runtime._task.cancel()
            await asyncio.gather(runtime._task, return_exceptions=True)

    asyncio.run(check())


def test_the_durable_endpoint_is_registered(runtime: ServerRuntime) -> None:
    from src.server.app import create_app
    from src.server.config import ServerConfig

    app = create_app(runtime, ServerConfig(password='x'), dist=runtime.composition.root / 'missing')

    assert '/api/session/durable' in {getattr(route, 'path', '') for route in app.routes}


def test_the_pending_route_reports_the_same_rows_as_a_durable_read(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The queue-only read and the durable one must not speak two vocabularies.

    A screen mid-turn learns of a queued message from an event that carries no text,
    so it asks for the rows — and only the rows. This route answers with the same
    projection `durable['pending']` carries: same rows, same keys, so a client can
    swap one read for the other without learning a second shape.
    """
    import uuid

    request_id = str(uuid.uuid4())
    store = runtime.composition.store
    store.accept_input(runtime.session, request_id, 'waiting while nothing consumes', [], 'turn')

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            pending = await client.get('/api/session/pending')
            durable = await client.get('/api/session/durable')
            assert pending.status_code == 200, pending.text
            assert durable.status_code == 200, durable.text
            rows = pending.json()['pending']
            assert [row['request_id'] for row in rows] == [request_id], 'the waiting row is missing'
            assert rows == durable.json()['pending'], 'the two reads disagree about the wait'
            assert rows[0]['state'] in views.WAITING_STATES
            assert rows[0]['delivered'] is False

    asyncio.run(go())


async def _the_loop_stays_free(
    client: httpx.AsyncClient,
    request: Coroutine[Any, Any, httpx.Response],
    entered: threading.Event,
    release: threading.Event,
) -> httpx.Response:
    """Starts a projection and proves the loop answered while it was still running.

    ``entered`` is set from inside the projection, so the read has begun; ``release``
    is what lets it finish. The watchdog releases it after two seconds so a
    regression to an inline projection fails this test instead of hanging the suite.
    """
    watchdog = threading.Timer(2.0, release.set)
    watchdog.start()
    response = asyncio.create_task(request)
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 15), 20), 'the projection never started'
        state = await asyncio.wait_for(client.get('/api/state'), 5)
        assert state.status_code == 200, 'the loop did not answer while the projection ran'
        assert not response.done(), 'the projection ran on the loop, so nothing else could answer'
    finally:
        release.set()
        watchdog.cancel()
    return await asyncio.wait_for(response, 5)


def test_the_durable_projection_runs_off_the_event_loop(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The durable read projected the whole conversation on the loop: 231 ms measured.

    Everything else — the stream's deltas, every other route — waited behind it. The
    projection now runs in a worker thread and the loop is free, which is what this
    test holds it to: the projection is blocked on a real thread event and the state
    route still answers.
    """
    real = views.durable_session
    entered = threading.Event()
    release = threading.Event()

    def blocked(database: Any, session_id: str) -> dict[str, Any]:
        entered.set()
        assert release.wait(15), 'the projection was never released'
        return real(database, session_id)

    monkeypatch.setattr(views, 'durable_session', blocked)
    # A draft session has no row to project, and the route would answer 404 without
    # ever running the projection this test holds open.
    runtime.composition.store.accept_input(runtime.session, str(uuid.uuid4()), 'stored', [], 'turn')

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await _the_loop_stays_free(client, client.get('/api/session/durable'), entered, release)
            assert response.status_code == 200, response.text

    asyncio.run(go())


def test_the_whole_transcript_projection_runs_off_the_event_loop(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a window the transcript route projects the whole conversation.

    That is the web client's read, and it costs what the conversation has. A page
    costs 1.6 ms and stays on the loop; the whole read does not.
    """
    real = views.transcript_window
    entered = threading.Event()
    release = threading.Event()

    def blocked(messages: Any, *, limit: int | None = None, before: int | None = None, **kwargs: Any) -> Any:
        if limit is None:
            entered.set()
            assert release.wait(15), 'the projection was never released'
        return real(messages, limit=limit, before=before, **kwargs)

    monkeypatch.setattr(views, 'transcript_window', blocked)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await _the_loop_stays_free(client, client.get('/api/session/transcript'), entered, release)
            assert response.status_code == 200, response.text

    asyncio.run(go())


def test_send_without_a_request_id_still_works(runtime: ServerRuntime) -> None:
    async def go():
        result = await runtime.send('hola')
        await asyncio.wait_for(runtime.wait(), 5)
        return result

    result = asyncio.run(go())
    assert result['accepted'] == 'turn'
    assert isinstance(result['request_id'], str)


def test_send_reuses_the_client_request_id_and_does_not_duplicate(runtime: ServerRuntime) -> None:
    import uuid

    request_id = str(uuid.uuid4())

    async def go():
        first = await runtime.send('hola', request_id=request_id)
        await asyncio.wait_for(runtime.wait(), 5)
        second = await runtime.send('hola', request_id=request_id)
        await asyncio.wait_for(runtime.wait(), 5)
        return first, second

    first, second = asyncio.run(go())
    assert first['request_id'] == second['request_id'] == request_id
    assert second['duplicate'] is True
    from src.engine.storage.accepted_inputs import AcceptedInputs

    assert len(AcceptedInputs(runtime.composition.store.database).list(runtime.session.id)) == 1


def test_send_rejects_an_empty_request_id(runtime: ServerRuntime) -> None:
    async def go():
        with pytest.raises(ValueError, match='request_id must not be empty'):
            await runtime.send('hola', request_id='')

    asyncio.run(go())


def test_the_same_request_id_in_two_sessions_is_independent(runtime: ServerRuntime) -> None:
    import uuid

    from src.engine.storage.accepted_inputs import AcceptedInputs

    store = runtime.composition.store
    first = store.create_session()
    second = store.create_session()
    request_id = str(uuid.uuid4())
    store.accept_input(first, request_id, 'one', [], 'turn')
    store.accept_input(second, request_id, 'two', [], 'turn')
    assert len(AcceptedInputs(store.database).list(first.id)) == 1
    assert len(AcceptedInputs(store.database).list(second.id)) == 1


def test_send_rejects_a_request_id_reused_with_different_text(runtime: ServerRuntime) -> None:
    import uuid

    request_id = str(uuid.uuid4())

    async def go():
        await runtime.send('first', request_id=request_id)
        await asyncio.wait_for(runtime.wait(), 5)
        with pytest.raises(ValueError):
            await runtime.send('different', request_id=request_id)

    asyncio.run(go())


# --- the turn command over HTTP ---------------------------------------------
#
# These are contract tests: they drive the real ASGI app through an in-process
# transport and read the queue back over HTTP, so a behaviour that only exists
# when `accept_input` is called directly is not covered by them.

_PASSWORD = 'secret-pass'


def _png(color: tuple[int, int, int] = (255, 0, 0), size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


def _png_of_exactly(size: int) -> bytes:
    """A valid PNG whose length is exactly ``size`` bytes.

    The padding is a private ``tEXt`` chunk: it changes the file size without
    touching the pixels, so a byte-ceiling test does not need a real large image.
    """
    base = _png()
    chunk_overhead = 12  # length, type and CRC of the inserted chunk
    text = b'padding\x00' + b'x' * (size - len(base) - chunk_overhead - len(b'padding\x00'))
    chunk = struct.pack('>I', len(text)) + b'tEXt' + text + struct.pack('>I', zlib.crc32(b'tEXt' + text))
    payload = base[:-12] + chunk + base[-12:]
    assert len(payload) == size
    return payload


def _image(payload: bytes, media_type: str = 'image/png') -> dict[str, str]:
    return {'media_type': media_type, 'data': base64.b64encode(payload).decode('ascii')}


_CHUNK_BYTES = 256
"""How much of a streamed body the transport hands over per ASGI message."""


async def _streamed(payload: bytes) -> AsyncIterator[bytes]:
    """A request body the transport cannot give a `Content-Length` for."""
    for start in range(0, len(payload), _CHUNK_BYTES):
        yield payload[start : start + _CHUNK_BYTES]


@asynccontextmanager
async def _signed_in(runtime: ServerRuntime, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """A logged-in client for the real app, over the ASGI transport (no socket)."""
    app = create_app(runtime, ServerConfig(password=_PASSWORD), dist=tmp_path / 'missing')
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url='http://sigil.test') as client:
        login = await client.post('/api/login', json={'password': _PASSWORD})
        assert login.status_code == 200, login.text
        yield client


async def _queue(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get('/api/session/durable')
    assert response.status_code == 200, response.text
    return response.json()['queue']


def test_the_turn_route_acknowledges_images_only_after_commit(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post(
                '/api/turn',
                json={'text': 'mira [Image #1] y [Image #2]', 'images': [_image(_png()), _image(_png((0, 0, 255)))]},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert len(body['images']) == 2
            queue = await _queue(client)
            assert [entry['request_id'] for entry in queue] == [body['request_id']]
            assert queue[0]['image_count'] == 2

    asyncio.run(go())


def test_a_retried_request_with_the_same_images_is_one_acceptance(runtime: ServerRuntime, tmp_path: Path) -> None:
    images = [_image(_png()), _image(_png((0, 255, 0)))]

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            first = await client.post('/api/turn', json={'text': 'look', 'images': images})
            assert first.status_code == 200, first.text
            request_id = first.json()['request_id']
            retry = await client.post('/api/turn', json={'text': 'look', 'request_id': request_id, 'images': images})
            assert retry.status_code == 200, retry.text
            assert retry.json()['duplicate'] is True
            assert retry.json()['request_id'] == request_id
            assert [image['id'] for image in retry.json()['images']] == [
                image['id'] for image in first.json()['images']
            ]
            await asyncio.wait_for(runtime.wait(), 5)
            queue = await _queue(client)
            assert len(queue) == 1
            assert queue[0]['image_count'] == 2

    asyncio.run(go())


def test_a_retried_request_with_different_images_is_a_conflict(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            first = await client.post('/api/turn', json={'text': 'look', 'images': [_image(_png())]})
            assert first.status_code == 200, first.text
            request_id = first.json()['request_id']
            conflict = await client.post(
                '/api/turn',
                json={'text': 'look', 'request_id': request_id, 'images': [_image(_png((0, 0, 255)))]},
            )
            assert conflict.status_code == 400
            assert 'different content' in conflict.json()['error']
            await asyncio.wait_for(runtime.wait(), 5)
            queue = await _queue(client)
            assert len(queue) == 1
            assert queue[0]['image_count'] == 1

    asyncio.run(go())


def test_more_than_ten_images_are_refused(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/turn', json={'text': 'first'})).status_code == 200
            refused = await client.post('/api/turn', json={'text': 'too many', 'images': [_image(_png())] * 11})
            assert refused.status_code == 400
            assert 'at most 10' in refused.json()['error']
            assert len(await _queue(client)) == 1

    asyncio.run(go())


def test_an_image_over_four_mebibytes_is_refused_before_decoding(runtime: ServerRuntime, tmp_path: Path) -> None:
    oversized = b'\x89PNG\r\n\x1a\n' + b'\x00' * (4 * 1024 * 1024)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/turn', json={'text': 'first'})).status_code == 200
            refused = await client.post('/api/turn', json={'text': 'big', 'images': [_image(oversized)]})
            assert refused.status_code == 400
            assert '4 MiB' in refused.json()['error']
            assert len(await _queue(client)) == 1

    asyncio.run(go())


def test_exactly_ten_images_are_accepted(runtime: ServerRuntime, tmp_path: Path) -> None:
    """Ten is the ceiling, not the first refused count: the check is `> 10`."""
    images = [_image(_png((value, value, value))) for value in range(10)]

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            accepted = await client.post('/api/turn', json={'text': 'ten', 'images': images})
            assert accepted.status_code == 200, accepted.text
            await asyncio.wait_for(runtime.wait(), 5)
            queue = await _queue(client)
            assert len(queue) == 1
            assert queue[0]['image_count'] == 10

    asyncio.run(go())


def test_an_image_of_exactly_four_mebibytes_is_accepted_and_one_byte_more_is_refused(
    runtime: ServerRuntime, tmp_path: Path
) -> None:
    """The byte ceiling is inclusive: `len(raw) > MAX_IMAGE_BYTES` refuses the next byte."""
    limit = 4 * 1024 * 1024
    at_limit = _png_of_exactly(limit)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            accepted = await client.post('/api/turn', json={'text': 'edge', 'images': [_image(at_limit)]})
            assert accepted.status_code == 200, accepted.text
            refused = await client.post(
                '/api/turn', json={'text': 'over', 'images': [_image(_png_of_exactly(limit + 1))]}
            )
            assert refused.status_code == 400
            assert '4 MiB' in refused.json()['error']
            await asyncio.wait_for(runtime.wait(), 5)
            assert [entry['text'] for entry in await _queue(client)] == ['edge']

    asyncio.run(go())


def test_a_payload_that_is_not_base64_is_refused(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/turn', json={'text': 'first'})).status_code == 200
            refused = await client.post(
                '/api/turn',
                json={'text': 'broken', 'images': [{'media_type': 'image/png', 'data': '%%% not base64 %%%'}]},
            )
            assert refused.status_code == 400
            assert 'base64' in refused.json()['error']
            assert len(await _queue(client)) == 1

    asyncio.run(go())


def test_an_unsupported_media_type_is_refused(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/turn', json={'text': 'first'})).status_code == 200
            refused = await client.post(
                '/api/turn', json={'text': 'svg', 'images': [_image(_png(), media_type='image/svg+xml')]}
            )
            assert refused.status_code == 400
            assert 'image/svg+xml' in refused.json()['error']
            assert len(await _queue(client)) == 1

    asyncio.run(go())


def test_images_submitted_over_http_reach_the_turn_history(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> list[dict]:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post(
                '/api/turn',
                json={'text': 'compare [Image #1]', 'images': [_image(_png(size=(6, 4)))]},
            )
            assert response.status_code == 200, response.text
            await asyncio.wait_for(runtime.wait(), 5)
            transcript = await client.get('/api/session/transcript')
            assert transcript.status_code == 200
            return transcript.json()['items']

    items = asyncio.run(go())
    user = next(item for item in items if item['kind'] == 'user')
    assert user['text'] == 'compare [Image #1]'
    assert [attachment['media_type'] for attachment in user['attachments']] == ['image/png']
    assert user['attachments'][0]['width'] == 6


def test_a_failed_commit_is_not_acknowledged(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/turn', json={'text': 'first'})).status_code == 200

            def boom(*_args: object, **_kwargs: object) -> None:
                raise OSError('disk is full')

            runtime.composition.store.accept_input = boom  # type: ignore[method-assign]
            failed = await client.post('/api/turn', json={'text': 'lost', 'images': [_image(_png())]})
            assert failed.status_code >= 500
            queue = await _queue(client)
            assert [entry['text'] for entry in queue] == ['first']

    asyncio.run(go())


def test_a_command_body_over_the_ceiling_is_refused_without_writing(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 64 MiB cap is exercised with a tiny one: the ceiling, not the size matters."""
    from src.server import app as app_module

    monkeypatch.setattr(app_module, 'MAX_COMMAND_BYTES', 512)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/turn', json={'text': 'first'})).status_code == 200
            refused = await client.post('/api/turn', json={'text': 'x' * 4096})
            assert refused.status_code == 413
            assert 'larger than 512 bytes' in refused.json()['error']
            assert [entry['text'] for entry in await _queue(client)] == ['first']

    asyncio.run(go())


def _chunked_scope() -> dict[str, object]:
    """A request scope shaped like a chunked `POST /api/turn`: no `Content-Length`."""
    return {
        'type': 'http',
        'method': 'POST',
        'path': '/api/turn',
        'headers': [(b'content-type', b'application/json')],
    }


def test_a_chunked_body_is_refused_at_the_ceiling_while_it_arrives() -> None:
    """The branch without a length: count each chunk and stop the read at the cap.

    This drives the middleware directly, with the same scope/receive/send shape an
    ASGI server uses. `_BodyTooLarge` derives from `BaseException` precisely so the
    middleware catches it before FastAPI's `except Exception` around
    `request.json()` can turn it into a 400, which is what
    `test_a_streamed_body_over_the_ceiling_is_refused_without_writing` proves
    through the composed app.
    """
    limit = 512
    seen: list[bytes] = []
    sent: list[dict[str, object]] = []
    chunks = [b'x' * 256] * 8

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            seen.append(message['body'])
            if not message.get('more_body'):
                break
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    async def receive() -> Message:
        return {'type': 'http.request', 'body': chunks.pop(0), 'more_body': bool(chunks)}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(BodyLimitMiddleware(downstream, limit=limit)(_chunked_scope(), receive, send))

    starts = [message for message in sent if message['type'] == 'http.response.start']
    assert [start['status'] for start in starts] == [413]
    assert json.loads(sent[1]['body']) == {'error': f'The request body is larger than {limit} bytes.'}
    # The wrapped app never saw more than the ceiling, and never answered.
    assert sum(len(chunk) for chunk in seen) == limit
    assert len(seen) == 2


def test_a_body_that_grows_after_the_response_started_is_not_refused_twice() -> None:
    """Defence in depth: a response already going out cannot be replaced by a 413."""
    from src.server.app import _BodyTooLarge

    limit = 512
    sent: list[dict[str, object]] = []
    chunks = [b'x' * 256] * 4

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        while True:
            message = await receive()
            if not message.get('more_body'):
                break

    async def receive() -> Message:
        return {'type': 'http.request', 'body': chunks.pop(0), 'more_body': bool(chunks)}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    with pytest.raises(_BodyTooLarge):
        asyncio.run(BodyLimitMiddleware(downstream, limit=limit)(_chunked_scope(), receive, send))

    # The 200 start already went out, so the middleware adds nothing after it.
    assert sent == [{'type': 'http.response.start', 'status': 200, 'headers': []}]


def test_a_chunked_body_under_the_ceiling_reaches_the_wrapped_app() -> None:
    """The same counting must let a chunked body below the cap through unchanged."""
    limit = 512
    payload = json.dumps({'text': 'hola'}).encode('utf-8')
    received: list[bytes] = []
    sent: list[dict[str, object]] = []
    chunks = [payload[index : index + 4] for index in range(0, len(payload), 4)]

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            received.append(message['body'])
            if not message.get('more_body'):
                break
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    async def receive() -> Message:
        return {'type': 'http.request', 'body': chunks.pop(0), 'more_body': bool(chunks)}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(BodyLimitMiddleware(downstream, limit=limit)(_chunked_scope(), receive, send))

    assert [message['status'] for message in sent if message['type'] == 'http.response.start'] == [200]
    assert b''.join(received) == payload


def test_a_streamed_body_over_the_ceiling_is_refused_without_writing(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chunked path through the real app: 413 at the ceiling, nothing accepted."""
    from src.server import app as app_module

    monkeypatch.setattr(app_module, 'MAX_COMMAND_BYTES', 512)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/turn', json={'text': 'first'})).status_code == 200
            request = client.build_request(
                'POST',
                '/api/turn',
                content=_streamed(json.dumps({'text': 'x' * 4096}).encode('utf-8')),
                headers={'content-type': 'application/json'},
            )
            # The premise of this test: no length to inspect, only bytes to count.
            assert 'content-length' not in request.headers
            refused = await client.send(request)
            assert refused.status_code == 413, refused.text
            assert 'larger than 512 bytes' in refused.json()['error']
            assert [entry['text'] for entry in await _queue(client)] == ['first']

    asyncio.run(go())


def test_a_streamed_body_under_the_ceiling_is_accepted(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counting the bytes must not refuse a chunked body that stays under the cap."""
    from src.server import app as app_module

    monkeypatch.setattr(app_module, 'MAX_COMMAND_BYTES', 512)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            request = client.build_request(
                'POST',
                '/api/turn',
                content=_streamed(json.dumps({'text': 'hola'}).encode('utf-8')),
                headers={'content-type': 'application/json'},
            )
            assert 'content-length' not in request.headers
            accepted = await client.send(request)
            assert accepted.status_code == 200, accepted.text
            await asyncio.wait_for(runtime.wait(), 5)
            assert [entry['text'] for entry in await _queue(client)] == ['hola']

    asyncio.run(go())


def test_an_unauthenticated_body_is_refused_before_the_ceiling(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auth runs outside the cap: a stranger is answered 401, not told the limit."""
    from src.server import app as app_module

    monkeypatch.setattr(app_module, 'MAX_COMMAND_BYTES', 64)
    app = create_app(runtime, ServerConfig(password=_PASSWORD), dist=tmp_path / 'missing')
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url='http://sigil.test') as client:
            return await client.post('/api/turn', json={'text': 'x' * 4096})

    response = asyncio.run(go())
    assert response.status_code == 401


# --- the ownership lock the runtime holds, own or adopted -------------------

ROOT = Path(__file__).resolve().parents[1]
"""The checkout, so a child process can import `src.*` without `PYTHONPATH`."""

OTHER_PROCESS = """
import sys

from src.server.ownership import OwnershipError, ServerOwnership

try:
    ServerOwnership({database!r}).acquire()
except OwnershipError:
    print('REFUSED')
else:
    print('ACQUIRED')
"""


def _other_process(database: str | Path) -> str:
    """Asks a real child process whether it may serve this store, and reports its answer."""

    result = subprocess.run(
        [sys.executable, '-c', OTHER_PROCESS.format(database=str(database))],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout.strip()


def test_an_adopted_ownership_is_not_acquired_a_second_time(composition: Composition) -> None:
    """The runtime takes the lock it was handed instead of opening the same file again.

    `flock` treats two descriptors of one process as two holders, so acquiring a
    second time would refuse this very process — after the engine was already
    built, which is the one place it must not fail.
    """

    ownership = ServerOwnership(composition.store.database.path)
    ownership.acquire()
    runtime = ServerRuntime(composition, EventHub(), ownership=ownership)
    try:
        runtime.start()

        assert ownership.held is True
        with pytest.raises(OwnershipError):
            ServerOwnership(composition.store.database.path).acquire()
    finally:
        runtime.close()

    assert ownership.held is False


def test_the_adopted_lock_is_what_refuses_another_process(composition: Composition) -> None:
    """The lock covers composing the engine, not only the serving loop after it."""

    ownership = ServerOwnership(composition.store.database.path)
    ownership.acquire()
    runtime = ServerRuntime(composition, EventHub(), ownership=ownership)
    try:
        runtime.start()
        # The engine already exists when this line runs, and this process excludes.
        assert _other_process(composition.store.database.path) == 'REFUSED'
    finally:
        runtime.close()

    assert _other_process(composition.store.database.path) == 'ACQUIRED'


def test_close_releases_an_ownership_the_runtime_took_itself(composition: Composition) -> None:
    runtime = ServerRuntime(composition, EventHub())
    runtime.start()

    runtime.close()

    assert _other_process(composition.store.database.path) == 'ACQUIRED'


def test_close_releases_an_adopted_ownership_that_never_started(composition: Composition) -> None:
    """A runtime that exists but never served must not keep the store locked."""

    ownership = ServerOwnership(composition.store.database.path)
    ownership.acquire()
    runtime = ServerRuntime(composition, EventHub(), ownership=ownership)

    runtime.close()

    assert ownership.held is False
    assert _other_process(composition.store.database.path) == 'ACQUIRED'


def test_close_twice_is_safe_for_an_adopted_ownership(composition: Composition) -> None:
    """Closing again releases nothing twice and raises nothing."""

    ownership = ServerOwnership(composition.store.database.path)
    ownership.acquire()
    runtime = ServerRuntime(composition, EventHub(), ownership=ownership)
    runtime.start()

    runtime.close()
    runtime.close()

    assert ownership.held is False
    assert _other_process(composition.store.database.path) == 'ACQUIRED'


# --- the engine decisions a client command calls ----------------------------
#
# `test_server_parity.py` proves the transport: status codes, projections and the
# state read back over HTTP. These prove the decision itself, at the level where
# the refusal is made, which is cheaper than a second HTTP pass and is where a
# regression in the rule would show up first.


def _node(composition: Composition, *, mode: str = 'sync') -> AgentNode:
    """A running node of the real tree, hanging from its root."""
    return composition.agent_trees.active.add(
        parent_id=None,
        name='reviewer',
        role=AgentRole.SUBAGENT,
        task='Review the diff.',
        mode=mode,
        model='test',
    )


def _composition_with_mcp(tmp_path: Path) -> Composition:
    """A composition whose config declares one MCP server, which is never loaded.

    The durable half of the toggle is the session override, and it has to be
    provable without starting `npx`: the layer is built on a worker by a serving
    app, and none of these decisions depends on it being there.
    """
    home = tmp_path / '.sigil'
    home.mkdir(exist_ok=True)
    (home / 'config.json').write_text(json.dumps({'mcp_servers': {'probe': {'command': 'npx'}}}), encoding='utf-8')
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )
    composition.meter.set_window(100_000)
    return composition


def test_a_subagent_is_stopped_along_with_everything_it_summoned(composition: Composition) -> None:
    parent = _node(composition)
    child = composition.agent_trees.active.add(
        parent_id=parent.id,
        name='reviewer',
        role=AgentRole.SUBAGENT,
        task='Child.',
        model='test',
    )

    message = composition.stop_subagent(parent.id)

    assert 'stopped' in message
    assert parent.status is NodeStatus.CANCELLED
    assert child.status is NodeStatus.CANCELLED


def test_the_engine_refuses_to_stop_the_session_agent(composition: Composition) -> None:
    """The rule lives in the summoner, so the root is refused below the route too."""

    root = composition.agent_trees.active.root
    assert root is not None

    with pytest.raises(SummonError, match='not a subagent'):
        composition.stop_subagent(root.id)

    assert root.status is NodeStatus.RUNNING


def test_the_engine_refuses_an_unknown_node_for_both_actions(composition: Composition) -> None:
    with pytest.raises(SummonError, match='No agent with id'):
        composition.stop_subagent('nope')

    with pytest.raises(SummonError, match='No agent with id'):
        composition.background_subagent('nope')


def test_backgrounding_flips_the_mode_and_keeps_the_node_working(composition: Composition) -> None:
    node = _node(composition)

    message = composition.background_subagent(node.id)

    assert 'background' in message
    assert node.mode == 'async'
    assert node.status is NodeStatus.RUNNING


def test_the_engine_refuses_to_background_the_session_agent(composition: Composition) -> None:
    """`detach` cannot know this one: the root has no parent waiting for an answer."""

    root = composition.agent_trees.active.root
    assert root is not None

    with pytest.raises(SummonError, match='not a subagent'):
        composition.background_subagent(root.id)

    assert root.mode == 'sync'


def test_a_finished_node_cannot_be_backgrounded(composition: Composition) -> None:
    node = _node(composition)
    composition.stop_subagent(node.id)

    with pytest.raises(SummonError, match='already finished'):
        composition.background_subagent(node.id)

    assert node.status is NodeStatus.CANCELLED


# --- the wake a finished subagent is owed ------------------------------------
#
# A background subagent outlives the turn that started it. The terminal answers
# that with a turn of its own (`TreeMixin._wake_from_subagents`); the service has
# to answer it too, or a report that lands after its turn stays unread for ever.
# `_deliver` is the engine's own call for it — the last thing `_execute` does for
# an async node — so these tests drive the real bell, not a second one.

WAKE_DEADLINE = 5


def _finished(composition: Composition, result: str, *, mode: str = 'async') -> AgentNode:
    """A subagent that ran, answered and finished with nobody in a turn."""
    node = _node(composition, mode=mode)
    node.finish(result)
    composition.summoner._deliver(node, result)
    return node


def _prompts(session: Any) -> list[str]:
    """Every user line of a stored conversation, in order."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    texts: list[str] = []
    for message in session.messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                content = part.content
                texts.append(content if isinstance(content, str) else str(content))
    return texts


def _turn_starts(runtime: ServerRuntime, after: int) -> list[Any]:
    return [event for event in runtime.hub.replay(after) if event.type == 'turn_started']


def test_a_report_that_lands_after_the_turn_wakes_a_turn_that_reads_it(runtime: ServerRuntime) -> None:
    """The gap this cut closes: the squad answers and nobody is in a turn to read it."""

    async def go() -> tuple[AgentNode, int]:
        await runtime.send('dispatch the researcher')
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        after = runtime.hub.last_seq
        node = _finished(runtime.composition, 'The tree reads bottom-up.')
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        return node, after

    node, after = asyncio.run(go())

    assert node.delivered is True, 'the woken turn consumed the report'
    assert len(_turn_starts(runtime, after)) == 1, 'exactly one turn reads the report'

    stored = runtime.composition.store.load(runtime.session.id)
    assert stored is not None
    prompts = _prompts(stored)
    assert any('The tree reads bottom-up.' in text for text in prompts), prompts
    assert any(WAKE_PROMPT in text for text in prompts), prompts
    # The synthesis is part of the conversation, not only of the run.
    assert stored.messages[-1].parts, 'the woken turn saved nothing'


def test_two_reports_of_one_squad_wake_one_turn(runtime: ServerRuntime) -> None:
    """The turn starts when the squad is quiet, not once per subagent that ends."""

    async def go() -> tuple[list[AgentNode], int]:
        await runtime.send('dispatch the squad')
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        after = runtime.hub.last_seq
        first = _node(runtime.composition, mode='async')
        second = _node(runtime.composition, mode='async')
        first.finish('The first report.')
        runtime.composition.summoner._deliver(first, 'The first report.')
        # The second is still working: waking now would be one turn per subagent.
        assert _turn_starts(runtime, after) == []
        second.finish('The second report.')
        runtime.composition.summoner._deliver(second, 'The second report.')
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        return [first, second], after

    nodes, after = asyncio.run(go())

    assert all(node.delivered for node in nodes)
    assert len(_turn_starts(runtime, after)) == 1, 'the whole set goes with one turn'

    stored = runtime.composition.store.load(runtime.session.id)
    assert stored is not None
    together = [text for text in _prompts(stored) if 'The first report.' in text]
    assert together and 'The second report.' in together[0], 'both reports reached the same prompt'


def test_a_closing_service_does_not_wake_a_turn(runtime: ServerRuntime) -> None:
    """Leaving is not a moment to start work: the flag waits, and nothing is spent."""

    async def go() -> tuple[AgentNode, int]:
        await runtime.send('dispatch the researcher')
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        after = runtime.hub.last_seq
        runtime._closing = True
        closed = _finished(runtime.composition, 'Too late.')
        assert await runtime._wake_from_subagents() is False
        return closed, after

    node, after = asyncio.run(go())

    assert node.delivered is False, 'the report is still waiting; nobody consumed it'
    assert [event.type for event in runtime.hub.replay(after)] == ['tree_changed', 'state_changed'], (
        'the closing service published the answer and started nothing'
    )
    assert _turn_starts(runtime, after) == [], 'no turn was started'


def test_a_cancelled_subagent_does_not_wake_a_turn(runtime: ServerRuntime) -> None:
    """A refused answer is not an answer: waking on it would re-run stopped work."""

    async def go() -> AgentNode:
        await runtime.send('dispatch the researcher')
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        node = _node(runtime.composition, mode='async')
        node.finish('Work somebody stopped on purpose.')
        # The cancellation arrived before anybody read the text: the node keeps it
        # and keeps `delivered` false, which is the state a wake must refuse.
        node.delivered = False
        node.cancel()
        assert await runtime._wake_from_subagents() is False
        return node

    node = asyncio.run(go())

    assert node.status is NodeStatus.CANCELLED
    assert node.delivered is False, 'nobody consumed it, because nobody may'
    stored = runtime.composition.store.load(runtime.session.id)
    assert stored is not None
    assert not any('Work somebody stopped on purpose.' in text for text in _prompts(stored))


def test_a_failed_subagent_wakes_a_turn(runtime: ServerRuntime) -> None:
    """The other half of the same rule: a failure is exactly what the parent needs."""

    async def go() -> AgentNode:
        await runtime.send('dispatch the researcher')
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        node = _node(runtime.composition, mode='async')
        node.fail('SummonError: the tool refused')
        node.delivered = False
        assert await runtime._wake_from_subagents() is True
        await asyncio.wait_for(runtime.wait(), WAKE_DEADLINE)
        return node

    node = asyncio.run(go())

    assert node.status is NodeStatus.FAILED
    assert node.delivered is True, 'the woken turn read the failure'
    stored = runtime.composition.store.load(runtime.session.id)
    assert stored is not None
    assert any('the tool refused' in text for text in _prompts(stored))


def test_the_service_and_the_terminal_say_the_same_wake_sentence() -> None:
    """The sentence is written twice on purpose, and this is what holds the copies together.

    `server.runtime.WAKE_PROMPT` is a copy of `ui.subagent_reports.WAKE_PROMPT` rather
    than an import, because the service must not depend on the client: that direction
    is what keeps the server headless. The terminal compares the stored report against
    this exact text to tell a finished subagent from a sentence the user typed, so a
    reword on one side alone would leave a woken turn painted as the user's own
    message. Nothing else compares the two — the product cannot, so the test does.
    """

    assert WAKE_PROMPT == TERMINAL_WAKE_PROMPT, (
        'The service and the terminal no longer say the same thing to a woken turn. The '
        'two copies exist because the server must not import the client, so this test is '
        'the only place that sees both: if they diverge, the terminal stops recognizing '
        'the turn a finished subagent woke and paints it as text the user wrote. Change '
        'both copies, or neither.'
    )


def test_external_files_is_written_on_the_row(composition: Composition) -> None:
    session = composition.store.create_session()
    composition.bind_session(session)

    composition.apply_external_files(True)

    assert composition.session.allow_external_files is True
    stored = composition.store.load(session.id)
    assert stored is not None
    assert stored.allow_external_files is True

    composition.apply_external_files(False)

    again = composition.store.load(session.id)
    assert again is not None
    assert again.allow_external_files is False


def test_the_mcp_override_is_written_on_the_row(tmp_path: Path) -> None:
    composition = _composition_with_mcp(tmp_path)
    session = composition.store.create_session()
    composition.bind_session(session)

    asyncio.run(composition.apply_mcp_override('probe', False))

    assert composition.session.mcp_overrides == {'probe': False}
    stored = composition.store.load(session.id)
    assert stored is not None
    assert stored.mcp_overrides == {'probe': False}


def test_an_unknown_mcp_server_changes_nothing(tmp_path: Path) -> None:
    composition = _composition_with_mcp(tmp_path)
    session = composition.store.create_session()
    composition.bind_session(session)

    with pytest.raises(LookupError, match='No MCP server named'):
        asyncio.run(composition.apply_mcp_override('ghost', False))

    assert composition.session.mcp_overrides == {}
    stored = composition.store.load(session.id)
    assert stored is not None
    assert stored.mcp_overrides == {}


# --- the MCP layer is installed, not merely built ----------------------------
#
# `_start_mcp` used to build the manager on a worker and drop it on the floor, so
# `MCPGateway.install` was never called and its ready event never fired. The
# symptom was not a wrong answer but no answer: `GET /api/mcp` awaits the layer,
# so it waited on a manager that was never coming. These two tests are the shape
# of that failure — one proves the route answers with the servers, the other that
# a load which fails answers too, instead of suspending the request forever.


def _mcp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One configured server, and only that one: the real home config is fenced off.

    `config_paths` falls back to `~/.sigil/config.json`, so without this the list
    this test reads would depend on the machine it ran on.
    """
    config = tmp_path / '.sigil' / 'config.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'mcp_servers': {'probe': {'command': 'npx', 'prefix': ''}}}), encoding='utf-8')
    monkeypatch.setenv('SIGIL_CONFIG', str(config))
    return config


def test_the_mcp_route_answers_with_the_servers_the_loader_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mcp_config(tmp_path, monkeypatch)
    composition = _composition_with_mcp(tmp_path)
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)

    async def go() -> list[str]:
        runtime.ensure_workers()
        async with _signed_in(runtime, tmp_path) as client:
            # Bounded on purpose: the defect was a request that never returned, and
            # a test that waits without a ceiling would hang the suite instead of
            # naming it.
            response = await asyncio.wait_for(client.get('/api/mcp'), timeout=30)
            assert response.status_code == 200, response.text
            return [server['name'] for server in response.json()['servers']]

    try:
        assert asyncio.run(go()) == ['probe']
    finally:
        runtime.abort()


def test_a_failed_mcp_load_answers_the_route_instead_of_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mcp_config(tmp_path, monkeypatch)
    composition = _composition_with_mcp(tmp_path)
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)

    def boom() -> None:
        raise RuntimeError('the layer could not start')

    monkeypatch.setattr(composition, 'mcp_loader', lambda: boom)

    async def go() -> int:
        runtime.ensure_workers()
        async with _signed_in(runtime, tmp_path) as client:
            response = await asyncio.wait_for(client.get('/api/mcp'), timeout=30)
            return response.status_code

    try:
        assert asyncio.run(go()) == 200
    finally:
        runtime.abort()
    # The reason is kept, so the failure is diagnosable rather than an empty list.
    assert any('the layer could not start' in error for error in composition.mcp.errors)


# --- stopping everything, and what the model can say about images ------------
#
# `abort` is about a turn. A session can still be spending with no turn running,
# which is what a background subagent does; leaving the terminal means the whole
# session, so the service needs an explicit operation for it. The image capability
# is the other projection the client cannot compute: an unconfigured model knows
# nothing about images, and saying *yes* there is worse than saying *unknown*.


def test_stop_all_stops_background_work_with_no_turn_to_abort(
    runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing `abort` cannot do while idle is stop what is still running.

    The summoner is the engine object that owns the subagents; `stop_all` has to
    reach it even when `abort` has nothing to cancel. `cancel_all`'s own effect is
    proven in `tests/test_agents_tree.py`; here the question is whether the
    session-level operation asks for it.
    """
    calls: list[str] = []
    summoner = runtime.composition.summoner
    original = summoner.cancel_all

    def record() -> None:
        calls.append('subagents')
        original()

    monkeypatch.setattr(summoner, 'cancel_all', record)

    assert runtime.stop_all() is False, 'nothing was running as a turn'
    assert calls == ['subagents']


def test_stop_all_still_stops_a_running_turn(runtime: ServerRuntime) -> None:
    """Stopping everything must not lose the turn half of the same request."""

    async def check() -> None:
        pending = asyncio.Event()
        runtime._task = asyncio.create_task(pending.wait())
        try:
            assert runtime.stop_all() is True
        finally:
            runtime._task.cancel()
            await asyncio.gather(runtime._task, return_exceptions=True)

    asyncio.run(check())


def test_the_stop_all_route_answers_and_reaches_the_summoner(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client asks over HTTP, so the route is the contract that has to exist."""
    calls: list[str] = []
    summoner = runtime.composition.summoner
    original = summoner.cancel_all

    def record() -> None:
        calls.append('subagents')
        original()

    monkeypatch.setattr(summoner, 'cancel_all', record)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            reply = await client.post('/api/stop-all')
            assert reply.status_code == 200, reply.text
            assert reply.json() == {'stopped': False}

    asyncio.run(go())
    assert calls == ['subagents']


# --- what a turn says while it waits on the model ---------------------------
#
# The consumer was proven healthy end to end — `accepted -> claimed -> turn_started`
# in a real subprocess — and the turn then said nothing at all: the service never
# subscribed to the retry notices the in-process client paints, so five attempts with
# a backoff of up to eight seconds published nothing and the screen read "Thinking"
# for minutes. These tests are the contract that silence broke.


def _flaky_model(failures: int = 2) -> tuple[FunctionModel, list[int]]:
    """A streaming model that fails transiently ``failures`` times and then answers.

    Streaming, because the service runs every turn with an event-stream handler and
    that is the shape a real provider failure arrives in.
    """
    from pydantic_ai.exceptions import ModelHTTPError, ModelServerError
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo

    error = ModelHTTPError.for_category(ModelServerError, status_code=502, model_name='test/model', body='bad gateway')
    calls: list[int] = []

    async def answer(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        calls.append(len(calls) + 1)
        if len(calls) <= failures:
            raise error
        yield 'final answer'

    return FunctionModel(stream_function=answer), calls


def test_a_transient_model_error_publishes_a_numbered_notice_per_attempt(runtime: ServerRuntime) -> None:
    """Each retry is a fact the clients can paint, not silence and a spinner."""
    from pydantic_ai import Agent

    retry = runtime.composition.model_retry
    retry.base_delay = 0.0  # the notice, not the clock, is what this test measures
    model, calls = _flaky_model(failures=2)
    runtime.composition.agent = Agent(model, instructions='x', capabilities=[retry])

    async def go() -> list[str]:
        await runtime.send('hola')
        await runtime.wait()
        return [str(event.data.get('text') or '') for event in runtime.hub.replay() if event.type == 'notice']

    texts = asyncio.run(go())

    assert len(calls) == 3, 'the model was not actually retried'
    retries = [text for text in texts if 'Transient model failure' in text]
    assert len(retries) == 2, retries
    assert f'retry 2/{retry.max_attempts}' in retries[0], retries
    assert f'retry 3/{retry.max_attempts}' in retries[1], retries
    assert 'HTTP 502' in retries[0], retries
    assert 'in 0.0s' in retries[0], retries


def test_a_notice_that_cannot_be_published_does_not_break_the_turn(
    runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sink is a client boundary: its failure is not the turn's failure."""
    from pydantic_ai import Agent

    retry = runtime.composition.model_retry
    retry.base_delay = 0.0
    model, calls = _flaky_model(failures=1)
    runtime.composition.agent = Agent(model, instructions='x', capabilities=[retry])
    attempted: list[str] = []
    original = runtime.hub.publish

    def publish(event_type: str, **data: Any) -> Any:
        text = str(data.get('text') or '')
        if event_type == 'notice' and 'Transient model failure' in text:
            attempted.append(text)
            raise RuntimeError('the client blew up')
        return original(event_type, **data)

    monkeypatch.setattr(runtime.hub, 'publish', publish)

    async def go() -> dict[str, Any]:
        await runtime.send('hola')
        await runtime.wait()
        return next(event.data for event in runtime.hub.replay() if event.type == 'turn_finished')

    finished = asyncio.run(go())

    assert attempted, 'the retry notice was never even attempted'
    assert len(calls) == 2, 'the retry did not happen'
    assert finished['status'] == 'ok', finished
    assert runtime.session.messages[-1].parts[0].content == 'final answer'


def test_a_turn_that_ends_in_error_leaves_its_row_terminal(runtime: ServerRuntime) -> None:
    """A failed turn must not leave a `claimed` row for the next start to find.

    The engine refuses to start, which is the shape of every failure that is not the
    model answering badly: nothing was delivered, so no checkpoint ever confirms the
    row, and only the end of the turn can close it. `stopped` is the terminal state
    the table allows from `claimed`; there is no failure state, and `interrupted` is
    reserved for reconciling a process that died (see `docs/architecture.md`).
    """
    from src.engine.storage.accepted_inputs import AcceptedInputs

    class Refusing:
        """An agent whose run cannot start: the engine's failure, not the model's."""

        def iter(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError('the engine could not start')

    runtime.composition.agent = Refusing()

    async def go() -> dict[str, Any]:
        await runtime.send('work')
        await runtime.wait()
        return next(event.data for event in runtime.hub.replay() if event.type == 'turn_finished')

    finished = asyncio.run(go())

    accepted = AcceptedInputs(runtime.composition.store.database)
    assert finished['status'] == 'error', finished
    assert [row.state for row in accepted.list(runtime.session.id)] == ['stopped']
    assert accepted.has_open() is False


def test_a_turn_that_says_nothing_says_once_that_it_is_still_waiting(
    runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow model is legitimate; an invisible one is not.

    The threshold is shortened, and the model is held on an event the test releases,
    so the notice is measured without sleeping through it and without a real model.
    """
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from src.server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, 'SLOW_TURN_NOTICE_SECONDS', 0.01)
    release = asyncio.Event()

    async def answer(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        await release.wait()
        yield 'final answer'

    runtime.composition.agent = Agent(FunctionModel(stream_function=answer), instructions='x')

    async def go() -> tuple[int, list[str]]:
        subscription = runtime.hub.subscribe()
        try:
            await runtime.send('hola')
            while True:
                event = await subscription.next(timeout=5)
                assert event is not None, 'the service stopped publishing'
                if event.type == 'notice' and 'still waiting' in str(event.data.get('text') or '').lower():
                    break
            release.set()
            await asyncio.wait_for(runtime.wait(), 5)
        finally:
            subscription.close()
        notices = [
            str(event.data.get('text') or '')
            for event in runtime.hub.replay()
            if event.type == 'notice' and 'still waiting' in str(event.data.get('text') or '').lower()
        ]
        finished = [event for event in runtime.hub.replay() if event.type == 'turn_finished']
        assert finished and finished[0].data['status'] == 'ok', finished
        return len(notices), notices

    count, notices = asyncio.run(go())

    assert count == 1, notices


def test_an_unconfigured_model_projects_an_unknown_image_capability() -> None:
    """The projection says *unknown*, so no client can turn it into *yes*.

    `ModelSnapshot.images` defaults to true, which is harmless inside the process
    that knows the model but wrong once it reaches a client as a fact.
    """
    from types import SimpleNamespace

    from src.server import views

    assert views.model_view(SimpleNamespace(is_configured=False, images=True))['images'] is None
    assert views.model_view(SimpleNamespace(is_configured=True, images=False))['images'] is False
    assert views.model_view(SimpleNamespace(is_configured=True, images=True))['images'] is True


# --- the model defaults the terminal could not edit --------------------------
#
# `/default-models` is the one command an attached terminal refuses to serve, because
# the two config files that decide what a profile starts on belong to the service.
# These prove the two decisions the routes expose — what each file says, and a write
# that lands in the file the destination names — at the level where they are made;
# `test_server_parity.py` proves the routes carry them.


def _defaults_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    """A composition whose two config files are inside the test's own home.

    `defaults_paths` resolves the user file from `~`, so `HOME` is pointed inside
    `tmp_path` before the engine is built: a developer's own `~/.sigil/config.json`
    must not decide what a test asserts.
    """
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('SIGIL_CONFIG', raising=False)
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )
    composition.meter.set_window(100_000)
    return composition


def _defaults_files(composition: Composition, tmp_path: Path) -> tuple[Path, Path]:
    """The two files the service reads, named the way it resolves them."""
    return composition.root / '.sigil' / 'config.json', tmp_path / 'home' / '.sigil' / 'config.json'


def _config(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def _entry(account: str = 'work', model: str = 'openai/gpt-5', effort: str = 'high') -> dict[str, str]:
    return {'account': account, 'model': model, 'effort': effort}


def test_the_defaults_projection_names_both_files_and_what_each_one_holds(tmp_path: Path) -> None:
    """Two files, not one merged answer: which file says it is what the screen shows."""
    from types import SimpleNamespace

    from src.engine.agents.model_defaults import read_defaults
    from src.server import views

    project, user = tmp_path / 'project' / 'config.json', tmp_path / 'home' / 'config.json'
    _config(project, {'agents': {'wizard': _entry('work', 'project/model')}})
    _config(
        user, {'default_model': _entry('studio', 'gpt-5', 'low'), 'agents': {'scout': _entry('work', 'fast', 'low')}}
    )

    projected = views.model_defaults_view(
        read_defaults(paths=(project, user)),
        accounts=[SimpleNamespace(alias='work')],
    )

    assert projected['destinations'] == ['project', 'global']
    files = {item['scope']: item for item in projected['files']}
    assert files['project']['path'] == str(project)
    assert files['project']['exists'] is True
    assert files['project']['agents'] == {'wizard': _entry('work', 'project/model')}
    assert files['global']['default'] == _entry('studio', 'gpt-5', 'low')
    assert files['global']['agents'] == {'scout': _entry('work', 'fast', 'low')}
    assert projected['targets'] == [
        {'target': None, 'effective': _entry('studio', 'gpt-5', 'low'), 'invalid': False, 'sources': ['global']},
        {'target': 'scout', 'effective': _entry('work', 'fast', 'low'), 'invalid': False, 'sources': ['global']},
        {'target': 'wizard', 'effective': _entry('work', 'project/model'), 'invalid': False, 'sources': ['project']},
    ]
    # The accounts the entries name, and whether the service has them: the screen
    # shows the provider next to an alias, and `studio` is one it cannot show.
    assert projected['accounts'] == [
        {'alias': 'studio', 'configured': False},
        {'alias': 'work', 'configured': True},
    ]
    assert projected['errors'] == []


def test_a_defaults_file_that_is_not_there_is_an_empty_file_and_not_an_error(tmp_path: Path) -> None:
    """A missing file is a state to show, with nothing in it: the first write creates it."""
    from src.engine.agents.model_defaults import read_defaults
    from src.server import views

    project, user = tmp_path / 'project' / 'config.json', tmp_path / 'home' / 'config.json'

    projected = views.model_defaults_view(read_defaults(paths=(project, user)), accounts=[])

    assert [item['scope'] for item in projected['files']] == ['project', 'global']
    assert [item['exists'] for item in projected['files']] == [False, False]
    assert [item['default'] for item in projected['files']] == [None, None]
    assert [item['agents'] for item in projected['files']] == [{}, {}]
    assert [item['target'] for item in projected['targets']] == [None]
    assert projected['accounts'] == []
    assert projected['errors'] == []
    assert not project.exists() and not user.exists(), 'projecting a file never creates it'


def test_a_defaults_file_the_reader_cannot_use_is_reported_instead_of_guessed(tmp_path: Path) -> None:
    """A file that does not parse is named, and an entry it rejects is not silently dropped."""
    from src.engine.agents.model_defaults import read_defaults
    from src.server import views

    project, user = tmp_path / 'project' / 'config.json', tmp_path / 'home' / 'config.json'
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text('{"agents": ', encoding='utf-8')
    _config(
        user,
        {
            'default_model': _entry('work', 'gpt-5', 'turbo'),
            'agents': {'wizard': _entry('work', 'gpt-5', 'turbo')},
        },
    )

    projected = views.model_defaults_view(read_defaults(paths=(project, user)), accounts=[])

    files = {item['scope']: item for item in projected['files']}
    assert files['project']['exists'] is True
    assert any('cannot read config' in error for error in files['project']['errors'])
    assert files['project']['agents'] == {}, 'a file that does not parse holds no values'
    assert files['global']['default_invalid'] is True
    assert files['global']['invalid_agents'] == ['wizard']
    assert any('effort' in error for error in files['global']['errors'])
    assert [item['target'] for item in projected['targets']] == [None, 'wizard'], (
        'an entry the reader rejected is still a target'
    )


def test_the_service_reads_the_defaults_of_the_workspace_it_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read follows the workspace and the home, which is the pair a person edits."""
    composition = _defaults_composition(tmp_path, monkeypatch)
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    project, user = _defaults_files(composition, tmp_path)
    _config(project, {'agents': {'wizard': _entry('work', 'project/model')}})
    _config(user, {'default_model': _entry('studio', 'gpt-5', 'low')})

    state = runtime.model_defaults()

    assert state.path_for(ConfigScope.PROJECT) == project
    assert state.path_for(ConfigScope.GLOBAL) == user
    assert state.effective('wizard') == AgentModelConfig('work', 'project/model', 'high')
    assert state.effective(None) == AgentModelConfig('studio', 'gpt-5', 'low')
    assert state.sources('wizard') == frozenset({ConfigScope.PROJECT})


def test_writing_a_default_persists_it_and_the_next_read_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write lands in the file the destination names, and the engine reads it after."""
    composition = _defaults_composition(tmp_path, monkeypatch)
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    project, user = _defaults_files(composition, tmp_path)
    _config(user, {'default_model': _entry('studio', 'gpt-5', 'low')})
    written = _entry('work', 'second/model', 'medium')

    state = runtime.save_model_default('scout', AgentModelConfig('work', 'second/model', 'medium'), ConfigScope.PROJECT)

    assert state.effective('scout') == AgentModelConfig('work', 'second/model', 'medium')
    assert state.sources('scout') == frozenset({ConfigScope.PROJECT})
    assert json.loads(project.read_text(encoding='utf-8'))['agents']['scout'] == written
    assert json.loads(user.read_text(encoding='utf-8')) == {'default_model': _entry('studio', 'gpt-5', 'low')}
    # The re-read is what makes it take effect without a restart: the next subagent
    # summoned reads `agents.<name>` from the config the composition holds.
    assert composition.mcp_config.agents['scout'] == AgentModelConfig('work', 'second/model', 'medium')

    cleared = runtime.save_model_default('scout', None, ConfigScope.PROJECT)

    assert cleared.effective('scout') is None
    assert 'scout' not in json.loads(project.read_text(encoding='utf-8')).get('agents', {})


def test_writing_a_default_is_refused_while_a_turn_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule the terminal's `/default-models` already applies: a turn refuses it.

    A subagent reads `agents.<name>` when it is summoned, so rewriting that entry
    under a running turn would change the model of a branch that has not started.
    The subagent criterion is `/mcp`'s and not this command's, so it is not added.
    """
    composition = _defaults_composition(tmp_path, monkeypatch)
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    project, _user = _defaults_files(composition, tmp_path)

    async def go() -> None:
        pending = asyncio.Event()
        runtime._task = asyncio.create_task(pending.wait())
        try:
            with pytest.raises(RuntimeError, match='turn is running'):
                runtime.save_model_default('scout', AgentModelConfig('work', 'm', 'high'), ConfigScope.PROJECT)
        finally:
            task = runtime._task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            runtime._task = None

    asyncio.run(go())

    assert not project.exists(), 'a refused write must not create the file'


def test_a_write_the_engine_refuses_keeps_its_own_sentence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config the engine cannot write to says why, in its own words, and stays as it was."""
    composition = _defaults_composition(tmp_path, monkeypatch)
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    project, _user = _defaults_files(composition, tmp_path)
    _config(project, {'agents': ['wizard']})

    with pytest.raises(RuntimeError, match='not an object'):
        runtime.save_model_default('scout', AgentModelConfig('work', 'm', 'high'), ConfigScope.PROJECT)

    assert json.loads(project.read_text(encoding='utf-8')) == {'agents': ['wizard']}


# --- one page of the transcript ---------------------------------------------
#
# `views.transcript_page` is the whole paging rule: everything else is a call and
# a projection. So it is tested as what it is — a function over items that are
# already built — and no app is mounted to prove arithmetic.


def _transcript_items() -> list[dict[str, Any]]:
    """A projection with the shape paging has to respect: indexes repeat.

    A message can carry several parts, and every item built from it keeps the same
    `index` — the message it came from, which is what a click maps to its rollback
    point. Positions and indexes are therefore not the same number, and a rule that
    confused them would either repeat an item or leave a hole.
    """
    return [
        {'kind': 'user', 'index': 0, 'text': 'one'},
        {'kind': 'assistant', 'index': 1, 'text': 'a'},
        {'kind': 'thinking', 'index': 1, 'text': 'b'},
        {'kind': 'assistant', 'index': 1, 'text': 'c'},
        {'kind': 'user', 'index': 2, 'text': 'two'},
        {'kind': 'assistant', 'index': 3, 'text': 'done'},
    ]


def test_a_page_ends_where_the_cursor_says() -> None:
    page = views.transcript_page(_transcript_items(), limit=2, before=4)

    assert page.items == _transcript_items()[2:4]
    assert page.first_index == 2
    assert page.has_more is True
    assert page.total == 6


def test_the_newest_page_is_the_end_of_the_transcript() -> None:
    """No cursor means the newest window, which is what a client opens with."""

    page = views.transcript_page(_transcript_items(), limit=2)

    assert page.items == _transcript_items()[-2:]
    assert page.first_index == 4
    assert page.has_more is True


def test_walking_the_cursor_back_rebuilds_the_transcript_without_holes() -> None:
    """Every page is preceded by exactly the items the previous one did not have.

    This is the property the whole contract exists for: a client that keeps asking
    for the chunk above `first_index` sees the conversation once, in order, and
    with the indexes it already knew.
    """

    pages: list[list[dict[str, Any]]] = []
    before: int | None = None
    while True:
        page = views.transcript_page(_transcript_items(), limit=2, before=before)
        pages.append(page.items)
        if not page.has_more:
            break
        before = page.first_index

    assert len(pages) == 3
    assert [item for page in reversed(pages) for item in page] == _transcript_items()


def test_a_page_wider_than_what_is_left_stops_at_the_bottom() -> None:
    page = views.transcript_page(_transcript_items(), limit=99, before=3)

    assert page.items == _transcript_items()[:3]
    assert page.first_index == 0
    assert page.has_more is False


def test_without_a_limit_the_transcript_comes_back_whole() -> None:
    """The web client has not migrated, so no window is the window it still gets."""

    page = views.transcript_page(_transcript_items())

    assert page.items == _transcript_items()
    assert page.first_index == 0
    assert page.has_more is False
    assert page.total == 6


def test_a_cursor_of_zero_is_an_empty_page() -> None:
    """The bottom of the transcript is a real answer, not a missing one."""

    page = views.transcript_page(_transcript_items(), limit=2, before=0)

    assert page.items == []
    assert page.first_index == 0
    assert page.has_more is False
    assert page.total == 6


def test_a_cursor_past_the_end_answers_the_newest_page() -> None:
    """A cursor the service has not reached yet is clamped, never a failure."""

    page = views.transcript_page(_transcript_items(), limit=2, before=99)

    assert page.items == _transcript_items()[-2:]
    assert page.first_index == 4
    assert page.has_more is True


# --- a window projected without projecting the whole conversation -----------
#
# The rule above is arithmetic over items that already exist. The route, though,
# used to build every item before cutting the window, which is O(transcript) per
# page. `transcript_window` applies the same rule to the messages: it counts what
# each message projects and builds only the parts the window returns. The old
# path stays here as the oracle.


def _window_messages() -> list[Any]:
    """Messages whose parts project to a known item list, empty parts included.

    A message with three parts spans three positions, so a page boundary can land
    inside it. A system prompt projects to nothing and must not move a position.
    """
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
        ThinkingPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    return [
        ModelRequest(parts=[UserPromptPart('one')]),  # positon 0
        ModelResponse(
            parts=[
                ThinkingPart('t1'),
                TextPart('a1'),
                ToolCallPart(tool_name='read_file', args={'path': 'a.py'}, tool_call_id='c1'),
            ]
        ),  # positions 1, 2, 3
        ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content='r1', tool_call_id='c1')]),  # position 4
        ModelRequest(parts=[SystemPromptPart('system')]),  # no item
        ModelResponse(parts=[ThinkingPart('t2'), TextPart('a2')]),  # positions 5, 6
        ModelRequest(parts=[UserPromptPart('two')]),  # position 7
    ]


def test_without_a_limit_a_window_is_the_whole_transcript() -> None:
    """The unmigrated client still gets everything, in the same order and shape."""

    messages = _window_messages()

    window = views.transcript_window(messages)

    assert window.items == views.transcript(messages)
    assert window.first_index == 0
    assert window.has_more is False
    assert window.total == 8


@pytest.mark.parametrize(
    ('limit', 'before'),
    [
        (None, None),
        (100, None),
        (2, None),
        (1, 3),
        (3, 5),
        (2, 6),
        (5, 99),
        (7, 8),
        (2, 0),
        (99, 0),
    ],
)
def test_a_window_is_identical_to_projecting_then_paging(limit: int | None, before: int | None) -> None:
    """Same items, same cursor, same total: the oracle is the old path itself."""

    messages = _window_messages()
    oracle = views.transcript_page(views.transcript(messages), limit=limit, before=before)

    window = views.transcript_window(messages, limit=limit, before=before)

    assert window.items == oracle.items
    assert window.first_index == oracle.first_index
    assert window.has_more == oracle.has_more
    assert window.total == oracle.total


def test_a_window_can_start_and_end_inside_one_message() -> None:
    """The cut is at part level, not message level: a message spans positions."""

    window = views.transcript_window(_window_messages(), limit=2, before=3)

    assert [item['text'] for item in window.items] == ['t1', 'a1']
    assert [item['index'] for item in window.items] == [1, 1]
    assert window.first_index == 1
    assert window.has_more is True
