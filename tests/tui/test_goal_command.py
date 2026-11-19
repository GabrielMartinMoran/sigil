"""The terminal-side ``/goal`` command, local and attached.

The command is plumbing: it parses one argument, changes the goal the session
carries, and paints one line. These tests use a minimal real store and a fake
service client; the real app is covered by the route and client tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from src.core.goal.state import Goal
from src.core.sessions.session_store import SessionStore
from src.ui.client.unexpected_response import UnexpectedResponse
from src.ui.mixins.commands_mixin import BUSY_COMMANDS, CommandsMixin


class _Chat:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []

    async def add_notice(self, text: str, *, style: str = 'white') -> None:
        self.notices.append((text, style))

    @property
    def text(self) -> str:
        return '\n'.join(text for text, _ in self.notices)


class _Host(CommandsMixin):
    _turn_active = False
    _client: object | None = None

    def __init__(self, tmp_path: Path) -> None:
        self._store = SessionStore(cwd=tmp_path, base_dir=tmp_path)
        self._session = self._store.draft_session()
        self.question: object | None = None
        self.refreshed = 0
        self.steers = 0
        self.turns = 0

    def steer(self, *args: object, **kwargs: object) -> None:
        self.steers += 1

    def start_turn(self, *args: object, **kwargs: object) -> None:
        self.turns += 1

    def _pending_question(self) -> object | None:
        return self.question

    async def refresh_service_state(self) -> None:
        self.refreshed += 1


def test_setting_a_goal_persists_it_on_a_draft_session(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    chat = _Chat()

    asyncio.run(host.handle_goal('--yolo Ship the CSV export', chat))

    assert host._session.goal == Goal(text='Ship the CSV export', yolo=True)
    assert 'Ship the CSV export' in chat.text
    reloaded = host._store.load(host._session.id)
    assert reloaded is not None
    assert reloaded.goal == host._session.goal


def test_status_reports_the_goal_the_session_carries(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host._session.goal = Goal(text='ship', status='active', revision=2)
    chat = _Chat()

    asyncio.run(host.handle_goal('status', chat))

    assert 'ship' in chat.text
    assert 'active' in chat.text
    assert 'revision 2' in chat.text
    assert host._session.goal == Goal(text='ship', status='active', revision=2)


def test_stopping_a_goal_persists_the_new_revision(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host._session.goal = Goal(text='ship', status='active', revision=2)
    host._store.save(host._session)
    chat = _Chat()

    asyncio.run(host.handle_goal('stop', chat))

    assert host._session.goal is not None
    assert host._session.goal.status == 'stopped'
    assert host._session.goal.revision == 3
    reloaded = host._store.load(host._session.id)
    assert reloaded is not None
    assert reloaded.goal is not None
    assert reloaded.goal.status == 'stopped'


def test_a_pending_question_refuses_activation_without_answering_it(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host.question = object()
    chat = _Chat()

    asyncio.run(host.handle_goal('--yolo Ship the export', chat))

    assert host._session.goal is None
    assert 'question' in chat.text.lower()
    assert host.question is not None, 'the question was not answered or dismissed'


def test_an_empty_objective_is_refused_before_any_store_write(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    chat = _Chat()

    asyncio.run(host.handle_goal('--yolo', chat))

    assert host._session.goal is None
    assert 'Provide a goal' in chat.text
    assert not host._session.messages


def test_attached_goals_travel_as_the_user_typed_them(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host._client = type(
        'Client',
        (),
        {'goal': AsyncMock(return_value={'notice': 'Goal set (revision 1): ship'})},
    )()
    chat = _Chat()

    asyncio.run(host.handle_goal('--yolo ship', chat))

    host._client.goal.assert_awaited_once_with('--yolo ship')
    assert 'Goal set' in chat.text
    assert host.refreshed == 1


def test_attached_status_does_not_repaint_the_service_state(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host._client = type('Client', (), {'goal': AsyncMock(return_value={'notice': 'No goal is set.'})})()
    chat = _Chat()

    asyncio.run(host.handle_goal('status', chat))

    host._client.goal.assert_awaited_once_with('status')
    assert host.refreshed == 0


def test_a_service_that_cannot_persist_is_reported_as_a_failure(tmp_path: Path) -> None:
    """No goal is painted when the service says the write did not happen."""
    host = _Host(tmp_path)
    host._client = type(
        'Client',
        (),
        {
            'goal': AsyncMock(
                side_effect=UnexpectedResponse(
                    'The service runtime does not expose the goal hook yet, so no goal was changed.'
                )
            )
        },
    )()
    chat = _Chat()

    asyncio.run(host.handle_goal('ship it', chat))

    assert host._session.goal is None
    assert chat.text.startswith('✖')
    assert 'Goal set' not in chat.text


def test_goal_is_allowed_while_a_turn_runs() -> None:
    assert '/goal' not in BUSY_COMMANDS


def test_goal_is_not_refused_when_busy_is_only_an_inflight_review(tmp_path: Path) -> None:
    """A reviewer is not a turn, so it must not close the command door."""
    host = _Host(tmp_path)
    host._client = object()
    host._service_state = {'busy': True}

    assert host.is_refused_while_busy('/goal') is False
    assert host.is_refused_while_busy('/model') is True


def test_a_goal_update_never_becomes_a_steer_or_a_turn(tmp_path: Path) -> None:
    host = _Host(tmp_path)
    host._client = type(
        'Client',
        (),
        {
            'goal': AsyncMock(return_value={'notice': 'Goal set (revision 1): ship'}),
            'send_turn': AsyncMock(),
        },
    )()
    chat = _Chat()

    asyncio.run(host.handle_goal('ship', chat))

    host._client.send_turn.assert_not_awaited()
    assert host.steers == 0
    assert host.turns == 0
