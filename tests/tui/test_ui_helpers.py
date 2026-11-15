"""Synchronization helpers must preserve UI event draining and bounded failure."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from textual.pilot import Pilot

from src.ui.widgets.chat_log import ChatLog
from tests.support.ui import ChatApp, fill_chat, wait_until


def test_fill_chat_creates_scrollable_content_without_a_widget_per_line() -> None:
    async def scenario() -> None:
        app = ChatApp()
        async with app.run_test(size=(80, 24)) as pilot:
            chat = app.query_one(ChatLog)
            await fill_chat(chat)
            await pilot.pause()
            assert len(chat.children) == 1
            assert chat.max_scroll_y > chat.size.height
            assert chat.is_vertical_scroll_end
            assert 'message 0' in str(chat.children[0].render())
            assert 'message 99' in str(chat.children[0].render())

    asyncio.run(scenario())


def test_wait_until_drains_events_even_when_condition_is_already_true() -> None:
    pilot = Mock(spec=Pilot)
    pilot.pause = AsyncMock()

    asyncio.run(wait_until(pilot, lambda: True))

    pilot.pause.assert_awaited_once_with()


def test_wait_until_stops_when_the_expected_state_arrives() -> None:
    pilot = Mock(spec=Pilot)
    pilot.pause = AsyncMock()

    asyncio.run(wait_until(pilot, lambda: pilot.pause.await_count == 3))

    assert pilot.pause.await_count == 3


def test_wait_until_raises_when_the_condition_never_arrives() -> None:
    async def yield_control() -> None:
        await asyncio.sleep(0)

    pilot = Mock(spec=Pilot)
    pilot.pause = AsyncMock(side_effect=yield_control)

    with pytest.raises(TimeoutError):
        asyncio.run(wait_until(pilot, lambda: False, timeout=0))
