"""The transcript keeps a gutter between its content and the scrollbar."""

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.core.sessions.session_store import SessionStore
from src.ui.sigil_app import SigilApp
from src.ui.widgets.chat.chat_log import ChatLog
from src.ui.widgets.chat.tool_call_block import ToolCallBlock


def test_chat_keeps_right_gutter_with_and_without_scrollbar(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=store.create_session())
        async with app.run_test(size=(80, 20)) as pilot:
            chat = app.query_one(ChatLog)
            await chat.add_notice('A short notice')
            await pilot.pause()
            assert not chat.show_vertical_scrollbar
            assert chat.children[-1].region.x == chat.region.x + 2
            assert chat.children[-1].region.right <= chat.region.right - 3

            for _ in range(20):
                await chat.add_notice('A notice that fills the transcript')
            await pilot.pause()
            assert chat.show_vertical_scrollbar
            assert chat.children[-1].region.x == chat.region.x + 2

    asyncio.run(scenario())


def test_rendered_tool_row_has_room_before_scrollbar(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=store.create_session())
        async with app.run_test(size=(90, 20)) as pilot:
            chat = app.query_one(ChatLog)
            for _ in range(20):
                await chat.add_notice('Earlier message')
            await chat.add_tool_call('shell', {'cmds': ['echo hello']})
            await chat.add_tool_result('shell', 'done')
            await pilot.pause()
            assert chat.show_vertical_scrollbar
            rows = [''.join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]
            row = next(line for line in rows if 'shell' in line)
            scrollbar = app.screen._compositor.find_widget(chat.vertical_scrollbar).region
            assert scrollbar.x == chat.region.right - 3
            assert chat.query('.notice').last().region.right <= scrollbar.x - 1
            assert 'echo hello' in row
            assert chat.query_one(ToolCallBlock).region.right <= scrollbar.x - 1

    asyncio.run(scenario())
