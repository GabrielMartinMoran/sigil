"""The service client's half of ``/goal``.

The client is a thin wire: it sends the argument as typed, returns the service's
answer and raises the typed conflict the command has to paint. The route's own
behaviour is covered by ``tests/server/test_goal_route.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.core.composition.composition import Composition
from src.server.runtime.server_runtime import ServerRuntime
from src.ui.client.service_busy import ServiceBusy
from tests.support.ui_service_client import _served


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    from tests.support.server_parity import _composition

    return _composition(tmp_path, monkeypatch)


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    from src.server.runtime.event_hub import EventHub

    return ServerRuntime(composition, EventHub(), require_ownership=False)


def test_the_client_sends_the_argument_as_typed_and_reads_the_goal(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            answer = await client.goal('--yolo Ship the export')

            assert answer['session']['goal'] == {
                'text': 'Ship the export',
                'yolo': True,
                'status': 'armed',
            }
            assert runtime.session.goal is not None
            assert runtime.session.goal.text == 'Ship the export'
            assert runtime.session.goal.revision == 1

            status = await client.goal('status')
            assert status['session']['goal']['text'] == 'Ship the export'
            assert 'revision 1' in status['notice']

    asyncio.run(go())


def test_the_client_surfaces_the_question_refusal_as_a_conflict(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime.session_runtime, '_pending_question', object())

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(ServiceBusy) as refusal:
                await client.goal('--yolo ship it')

            assert 'question' in str(refusal.value).lower()
            assert runtime.session.goal is None

    asyncio.run(go())
