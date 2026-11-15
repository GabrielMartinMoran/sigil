"""Synchronization helpers must preserve UI event draining and bounded failure."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from tests.support.ui import wait_for_turn


def test_wait_for_turn_waits_for_its_worker_not_unrelated_background_work() -> None:
    app = Mock()
    app._turn_active = True

    async def complete() -> None:
        app._turn_active = False

    turn = Mock()
    turn.name = 'run_turn'
    turn.wait = AsyncMock(side_effect=complete)
    background = Mock()
    background.name = 'session-title'
    background.wait = AsyncMock(side_effect=AssertionError('unrelated worker'))
    app.workers = [turn, background]

    asyncio.run(wait_for_turn(app))

    turn.wait.assert_awaited_once()
    background.wait.assert_not_awaited()


def test_wait_for_turn_rejects_an_active_turn_without_a_worker() -> None:
    app = Mock()
    app.workers = []
    app._turn_active = True

    with pytest.raises(AssertionError, match='still active'):
        asyncio.run(wait_for_turn(app))


def test_wait_for_turn_accepts_a_turn_that_already_completed() -> None:
    app = Mock()
    app.workers = []
    app._turn_active = False

    asyncio.run(wait_for_turn(app))


def test_wait_for_turn_has_a_deadline() -> None:
    app = Mock()
    app._turn_active = True
    turn = Mock()
    turn.name = 'run_turn'

    async def blocked() -> None:
        await asyncio.Event().wait()

    turn.wait = AsyncMock(side_effect=blocked)
    app.workers = [turn]

    with pytest.raises(TimeoutError):
        asyncio.run(wait_for_turn(app, timeout=0))


def test_wait_for_turn_propagates_worker_failure() -> None:
    app = Mock()
    app._turn_active = False
    turn = Mock()
    turn.name = 'run_turn'
    turn.wait = AsyncMock(side_effect=RuntimeError('turn failed'))
    app.workers = [turn]

    with pytest.raises(RuntimeError, match='turn failed'):
        asyncio.run(wait_for_turn(app))
