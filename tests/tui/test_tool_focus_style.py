"""Focusing an expandable tool must not underline its output."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog, ToolCallBlock


def test_should_not_underline_expanded_output_on_focus(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=store.create_session())
        async with app.run_test() as pilot:
            chat = app.query_one(ChatLog)
            await chat.add_tool_call('read_file', {'path': 'a'})
            row = app.query_one(ToolCallBlock)
            row.set_result('one\ntwo')
            row.focus()
            row.action_toggle_output()
            await pilot.pause()
            assert not row.styles.text_style.underline
            assert row._expanded

    asyncio.run(scenario())
