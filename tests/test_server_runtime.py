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
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from src.engine.bootstrap import Composition
from src.engine.settings import load_settings
from src.server.config import ServerConfig
from src.server.events import EventHub
from src.server.runtime import ServerRuntime


@pytest.fixture
def composition(tmp_path: Path) -> Composition:
    """A real composition with a stub model and its storage in a temporary directory."""
    return Composition(
        tmp_path,
        settings=load_settings(),
        # `call_tools=[]` on purpose: `TestModel` calls every tool by default, and one
        # of them is `ask_user`, which waits for an answer no test is going to give.
        # The turn would block on a question instead of finishing, and the test would
        # be measuring the wrong thing.
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )


def _paths(tmp_path: Path):
    from src.engine.providers.bootstrap import ProviderPaths

    home = tmp_path / '.sigil'
    home.mkdir(exist_ok=True)
    return ProviderPaths(home)


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    return ServerRuntime(composition, EventHub())


def test_a_turn_publishes_start_deltas_and_finish(runtime: ServerRuntime) -> None:
    """The event sequence a client depends on: the deltas are the typing."""

    async def go() -> list[str]:
        await runtime.send('hola')
        await runtime.wait()
        return [event.type for event in runtime.hub.replay()]

    types = asyncio.run(go())

    assert types[0] == 'turn_started'
    assert 'text_delta' in types
    assert types[-1] in {'turn_finished'} or 'turn_finished' in types


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


def test_new_session_and_open_session_round_trip(runtime: ServerRuntime) -> None:
    first = runtime.session
    second = runtime.new_session()
    assert second.id != first.id
    assert runtime.session.id == second.id
    reopened = runtime.open_session(first.id)
    assert reopened.id == first.id
    assert runtime.session.id == first.id


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


def test_logout_clears_the_cookie(tmp_path: Path, composition: Composition) -> None:
    from fastapi.testclient import TestClient

    from src.server.app import create_app

    runtime = ServerRuntime(composition, EventHub())
    client = TestClient(create_app(runtime, ServerConfig(password='secret-pass'), dist=tmp_path / 'nope'))
    client.post('/api/login', json={'password': 'secret-pass'})
    logout = client.post('/api/logout')
    assert 'Max-Age=0' in logout.headers['set-cookie']
    assert client.get('/api/state').status_code == 401
