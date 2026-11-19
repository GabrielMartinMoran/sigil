"""Contract tests for ``/api/goal``: parsing, persistence, refusal and access.

The route is the service half of the command. These tests drive the real ASGI app
and read the fact back from the session row, because a route that answers ``200``
and changes nothing is the failure this file exists to catch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from src.server.api.app import create_app
from src.server.launch.server_config import ServerConfig
from src.server.runtime.server_runtime import ServerRuntime
from tests.support.server_parity import PASSWORD, _composition, _runtime, _signed_in


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _composition(tmp_path, monkeypatch)


@pytest.fixture
def runtime(composition) -> ServerRuntime:
    return _runtime(composition)


def test_a_goal_is_set_persisted_and_read_back_by_the_service(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            before = await client.get('/api/goal')
            assert before.status_code == 200, before.text
            assert before.json()['session']['goal'] is None

            response = await client.post('/api/goal', json={'argument': '--yolo Ship the CSV export'})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body['session']['goal'] == {
                'text': 'Ship the CSV export',
                'yolo': True,
                'status': 'armed',
            }
            assert 'Ship the CSV export' in body['notice']
            assert runtime.session.goal is not None
            assert runtime.session.goal.revision == 1

            state = (await client.get('/api/state')).json()
            assert state['session']['goal'] == body['session']['goal']

            reloaded = runtime.composition.store.load(runtime.session.id)
            assert reloaded is not None
            assert reloaded.goal is not None
            assert reloaded.goal.text == 'Ship the CSV export'
            assert reloaded.goal.yolo is True

    asyncio.run(go())


def test_replacing_and_stopping_a_goal_move_the_revision(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            first = await client.post('/api/goal', json={'argument': 'ship it'})
            assert first.status_code == 200, first.text
            assert runtime.session.goal is not None
            assert runtime.session.goal.revision == 1

            replacement = await client.post('/api/goal', json={'argument': 'ship it differently'})
            assert replacement.status_code == 200, replacement.text
            assert replacement.json()['session']['goal']['yolo'] is False
            assert runtime.session.goal is not None
            assert runtime.session.goal.revision == 2

            stopped = await client.post('/api/goal', json={'argument': 'stop'})
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()['session']['goal'] == {
                'text': 'ship it differently',
                'yolo': False,
                'status': 'stopped',
            }
            assert runtime.session.goal is not None
            assert runtime.session.goal.revision == 3

            status = (await client.get('/api/goal')).json()
            assert status['session']['goal']['status'] == 'stopped'
            assert 'stopped' in status['notice']

    asyncio.run(go())


def test_an_empty_argument_is_a_rejection_and_changes_nothing(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/goal', json={'argument': '--yolo'})

            assert response.status_code == 400, response.text
            assert 'Provide a goal' in response.json()['error']
            assert runtime.session.goal is None

    asyncio.run(go())


def test_activation_is_a_conflict_while_a_question_waits(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service refuses instead of answering the question with a goal."""
    monkeypatch.setattr(runtime.session_runtime, '_pending_question', object())

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/goal', json={'argument': '--yolo ship it'})

            assert response.status_code == 409, response.text
            assert 'question' in response.json()['error'].lower()
            assert runtime.session.goal is None
            assert runtime.session_runtime.pending_question is not None

    asyncio.run(go())


def test_stopping_a_goal_is_allowed_while_a_question_waits(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            assert (await client.post('/api/goal', json={'argument': 'ship it'})).status_code == 200
            monkeypatch.setattr(runtime.session_runtime, '_pending_question', object())

            stopped = await client.post('/api/goal', json={'argument': 'stop'})

            assert stopped.status_code == 200, stopped.text
            assert stopped.json()['session']['goal']['status'] == 'stopped'
            assert runtime.session_runtime.pending_question is not None

    asyncio.run(go())


def test_a_goal_update_during_an_inflight_review_enqueues_no_steer(runtime: ServerRuntime, tmp_path: Path) -> None:
    """Busy due to a reviewer is not a turn, so the goal must not become a steer.

    The update is still applied: a review is judging the goal that is already there,
    and replacing it is a command, not a message for a run that does not exist yet.
    """

    async def go() -> None:
        held = asyncio.Event()
        review = asyncio.create_task(held.wait())
        runtime.session_runtime._goal_review_task = review
        try:
            assert runtime.busy is True
            assert runtime.session_runtime._run is None
            async with _signed_in(runtime, tmp_path) as client:
                response = await client.post('/api/goal', json={'argument': 'ship it'})

                assert response.status_code == 200, response.text
                assert runtime.session.goal is not None
                assert runtime.session.goal.text == 'ship it'
                assert runtime.session_runtime._enqueued == {}
                assert runtime.session_runtime._pending == {}
                assert sum(event.type == 'turn_started' for event in runtime.hub.replay()) == 0
        finally:
            held.set()
            await asyncio.gather(review, return_exceptions=True)
            runtime.session_runtime._goal_review_task = None

    asyncio.run(go())


def test_a_service_without_the_runtime_hook_says_so_and_changes_nothing(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A route that cannot persist must not answer as if it had."""
    from src.server.runtime.session_runtime import SessionRuntime

    monkeypatch.delattr(SessionRuntime, 'apply_goal', raising=False)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/goal', json={'argument': 'ship it'})

            assert response.status_code == 501, response.text
            assert 'does not expose the goal hook' in response.json()['error']
            assert runtime.session.goal is None

    asyncio.run(go())


def test_the_goal_route_keeps_the_services_authentication(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The command is inside the authenticated surface, like every other write."""
    app = create_app(runtime, ServerConfig(password=PASSWORD), dist=tmp_path / 'missing')
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def go() -> None:
        async with httpx.AsyncClient(
            transport=transport, base_url='http://sigil.test', headers={'x-sigil-session': runtime.session.id}
        ) as client:
            response = await client.post('/api/goal', json={'argument': 'ship it'})

            assert response.status_code == 401, response.text
            assert runtime.session.goal is None

    asyncio.run(go())
