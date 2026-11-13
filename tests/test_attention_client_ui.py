"""The attached terminal settles its attention from the service's tree.

The in-process seam and the attached terminal answer the same question — "is the
squad quiet?" — from different places, and the attached one has no summoner to
ask. Counting this process would report zero the moment the turn closed and call
the user back to a screen where the work goes on, so these tests pin the answer to
the projection the service sends.

No sleeps: the state read is a coroutine on a stand-in client, and the assertions
run the real `_finish_turn` the `turn_finished` event lands in.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.desktop_notifications import Attention
from src.ui.widgets.chat_log import ChatLog


class StubClient:
    """The calls `_finish_turn` makes through the client, and nothing else.

    A stand-in and not a mock, so the assertions are about what the app does with a
    projection rather than about which method was called. The stream yields nothing:
    these tests drive the state read directly instead of waiting on events.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self.state_payload = state

    async def state(self) -> dict[str, Any]:
        return self.state_payload

    async def transcript(self, *, limit: int | None = None, before: int | None = None) -> dict[str, Any]:
        """An empty newest page with nothing queued: the shape a fresh session has.

        The app attaches by painting a window rather than the whole durable session, so
        the stand-in answers the windowed read with the same empty truth.
        """
        return {'items': [], 'pending': [], 'first_index': 0, 'has_more': False}

    async def durable(self) -> dict[str, Any]:
        """An empty transcript with nothing queued: the shape a fresh session has."""
        return {'transcript': [], 'pending': []}

    async def events(self, *, after: int = 0):
        """No stream to follow: the state read is the whole fixture."""
        for _ in ():
            yield _


def _app(tmp_path: Path) -> SigilApp:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
    )


def _state(*statuses: str) -> dict[str, Any]:
    """A projection with one node per status: the session's own agent included."""
    return {
        'tree': {
            'root_id': 'root',
            'nodes': [{'id': f'n{index}', 'status': status} for index, status in enumerate(statuses)],
        }
    }


def test_should_count_running_nodes_from_the_projection_not_the_summoner(tmp_path: Path) -> None:
    """The attached terminal has no summoner worth asking; the service has the tree."""

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            # Two nodes working, two that will not change again.
            app._client = StubClient(_state('running', 'done', 'running', 'failed'))
            app._service_state = _state('running', 'done', 'running', 'failed')
            # Attached: the summoner is not consulted, and this app has none.
            assert app._summoner is None
            assert app.subagents_running() == 2

    asyncio.run(scenario())


def test_should_fall_back_to_the_summoner_without_a_client(tmp_path: Path) -> None:
    """The in-process seam keeps the answer it always had."""

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.run_test():
            app._client = None
            summoner = Mock()
            summoner.running.return_value = 3
            app._summoner = summoner
            assert app.subagents_running() == 3
            summoner.running.assert_called_once_with()

    asyncio.run(scenario())


def test_should_hold_the_notification_while_the_service_tree_still_works(tmp_path: Path) -> None:
    """Wiring, not just the policy: closing the turn is what asks for the signal."""

    async def scenario() -> None:
        app = _app(tmp_path)
        app.notify_attention = Mock()
        client = StubClient(_state('done', 'running'))
        app._client = client
        async with app.run_test():
            chat = app.query_one(ChatLog)
            # The turn is over; a subagent it summoned is not.
            await app._finish_turn({'status': 'ok'}, chat)
            app.notify_attention.assert_not_called()
            # The last node stops, and the next turn that closes delivers the signal
            # it was holding: the answer the user was waiting for has been written.
            client.state_payload = _state('done', 'done')
            await app._finish_turn({'status': 'ok'}, chat)
            app.notify_attention.assert_called_once_with(Attention.FINISHED)

    asyncio.run(scenario())


def test_should_report_a_failed_turn_and_stay_silent_on_cancellation(tmp_path: Path) -> None:
    """A failure is worth pulling the user back for; their own stop is not."""

    async def scenario() -> None:
        app = _app(tmp_path)
        app.notify_attention = Mock()
        app._client = StubClient(_state('done'))
        async with app.run_test():
            chat = app.query_one(ChatLog)
            await app._finish_turn({'status': 'error', 'error': 'boom'}, chat)
            app.notify_attention.assert_called_once_with(Attention.FAILED)
            app.notify_attention.reset_mock()
            # The user stopped it: no signal, however quiet the tree is.
            await app._finish_turn({'status': 'cancelled'}, chat)
            app.notify_attention.assert_not_called()

    asyncio.run(scenario())
