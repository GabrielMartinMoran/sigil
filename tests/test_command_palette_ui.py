"""Command palette keyboard flow and preservation of the composer's draft."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets import Input, OptionList

from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.command_palette_screen import CommandPaletteScreen
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.text_prompt_screen import TextPromptScreen


def make_app(tmp_path: Path) -> SigilApp:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'), store=store, session=store.create_session()
    )


def test_should_filter_palette_and_execute_without_losing_draft(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app.handle_command = AsyncMock()
        async with app.run_test(size=(100, 35)) as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = 'my draft'
            await pilot.press('ctrl+p')
            assert isinstance(app.screen, CommandPaletteScreen)
            app.screen.query_one(Input).value = 'context'
            await pilot.pause()
            assert app.screen.query_one(OptionList).option_count == 1
            await pilot.press('enter')
            await pilot.pause()
            app.handle_command.assert_awaited_once_with('/context', '')
            assert prompt.value == 'my draft'

    asyncio.run(scenario())


def test_should_request_worktree_name_and_dispatch_existing_command(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app.handle_command = AsyncMock()
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.press('ctrl+p')
            app.screen.query_one(Input).value = 'worktree'
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.screen, TextPromptScreen)
            app.screen.query_one(Input).value = 'feature-x'
            await pilot.press('enter')
            await pilot.pause()
            app.handle_command.assert_awaited_once_with('/new', '--worktree feature-x')

    asyncio.run(scenario())


def test_should_show_friendly_names_and_filter_by_name_or_slash_command(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app.handle_command = AsyncMock()
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.press('ctrl+p')
            screen = app.screen
            field = screen.query_one(Input)
            options = screen.query_one(OptionList)
            field.value = 'new session'
            await pilot.pause()
            assert options.option_count == 2
            row = options.get_option_at_index(0).prompt
            assert row.plain.startswith('New session (/new)')
            assert any(span.style == 'dim' and '/new' in row.plain[span.start : span.end] for span in row.spans)
            assert options.get_option_at_index(1).prompt.plain.startswith('New session in a worktree (/new --worktree)')
            field.value = '/model'
            await pilot.pause()
            assert options.option_count == 1
            assert options.get_option_at_index(0).prompt.plain.startswith('Choose model (/model)')
            await pilot.press('enter')
            await pilot.pause()
            app.handle_command.assert_awaited_once_with('/model', '')

    asyncio.run(scenario())


def test_should_set_external_file_access_from_palette(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 35)) as pilot:
            for query, expected in [('allow outside', True), ('deny outside', False)]:
                await pilot.press('ctrl+p')
                app.screen.query_one(Input).value = query
                await pilot.pause()
                assert app.screen.query_one(OptionList).option_count == 1
                await pilot.press('enter')
                await pilot.pause()
                assert app._session.allow_external_files is expected
                assert app._store.load(app._session.id).allow_external_files is expected

    asyncio.run(scenario())
