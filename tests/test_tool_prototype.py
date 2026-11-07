"""Tool rows follow the compact prototype without hiding failures."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.sessions.session_store import SessionStore
from src.engine.tooling.presentation import ToolSummary
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog, ToolCallBlock, ToolGroupHeader
from tests.ui_helpers import render_text


def test_should_align_arguments_and_render_commands_without_container_syntax() -> None:
    read = ToolCallBlock('read_file', {'path': 'src/app.py'})
    run = ToolCallBlock('run', {'cmds': ['pytest -q']})
    assert read._header().plain.index('src/app.py') == run._header().plain.index('pytest -q')
    assert "['" not in run._header().plain


def test_should_mark_a_failed_result_as_failed_without_calling_it_an_exception() -> None:
    block = ToolCallBlock('run', {'cmds': ['pytest']})
    block.set_summary(ToolSummary('1 failed', 'bad'), 100)
    block.set_result('NameError')
    assert block._state_mark() == '✗'
    assert not block.raised


def test_should_collapse_successful_rows_but_keep_failures_visible(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
        app = SigilApp(
            agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
            store=store,
            session=store.create_session(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chat = app.query_one(ChatLog)
            await chat.add_tool_call('read_file', {'path': 'a.py'})
            await chat.add_tool_call('run', {'cmds': ['pytest']})
            chat.set_tool_summary('read_file', ToolSummary('42 lines'), 10)
            await chat.add_tool_result('read_file', 'content')
            chat.set_tool_summary('run', ToolSummary('1 failed', 'bad'), 100)
            await chat.add_tool_result('run', 'NameError')
            header = app.query_one(ToolGroupHeader)
            header.focus()
            await pilot.press('enter')
            rows = list(app.query(ToolCallBlock))
            assert not rows[0].display
            assert rows[1].display
            assert '1 failed' in render_text(header._build())
            assert 'all passed' not in render_text(header._build())
            header.action_toggle_group()
            assert all(row.display for row in rows)
            rows[1].focus()
            await pilot.press('space')
            assert rows[1]._expanded
            assert '╭' in render_text(rows[1]._build())

    asyncio.run(scenario())
