"""Tests for the top sidebar panel: loaded skills and MCP counts."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.mcp.config.loader import load_config
from src.engine.mcp.manager.manager import MCPManager
from src.engine.sessions.session_store import SessionStore
from src.engine.skills.loaded import LoadedSkills
from src.ui.app import SigilApp
from src.ui.widgets.sidebar_panel import ContextState, MCPServerState, ModelState, SidebarPanel, WorkspaceState
from src.ui.widgets.status_bar import StatusBar


def _panel() -> SidebarPanel:
    return SidebarPanel()


def _configured() -> SidebarPanel:
    panel = _panel()
    panel._model = ModelState(alias='work', provider='OpenRouter', model='anthropic/x', effort='high')
    return panel


def test_panel_without_skills_says_so():
    text = _panel()._build().plain
    assert 'Loaded skills' in text
    assert 'none yet' in text


def test_panel_hides_the_context_until_a_model_is_configured():
    panel = _panel()
    panel._context_state = ContextState(percent=12.0, used=24_000, window=200_000)
    assert 'Context' not in panel._build().plain


def test_panel_shows_the_percentage_and_the_compacted_pair():
    panel = _configured()
    panel._context_state = ContextState(percent=66.0, used=132_000, window=200_000)
    text = panel._build().plain
    assert 'Context' in text
    assert '66%' in text
    assert '(132K / 200K)' in text


def test_panel_rounds_the_percentage_to_whole_numbers():
    panel = _configured()
    panel._context_state = ContextState(percent=12.4, used=24_800, window=200_000)
    assert '12%' in panel._build().plain
    assert '12.4%' not in panel._build().plain


def test_panel_shows_a_question_mark_without_a_window():
    panel = _configured()
    panel._context_state = ContextState(percent=None, used=None, window=None)
    text = panel._build().plain
    assert 'Context' in text
    assert '?' in text
    # Without a percentage there is no pair to compare against.
    assert '(' not in text


def test_panel_omits_the_pair_when_only_the_window_is_known():
    panel = _configured()
    panel._context_state = ContextState(percent=None, used=None, window=200_000)
    text = panel._build().plain
    assert 'Context' in text
    assert '?' in text
    assert '(/ 200K)' not in text
    assert '?' + ' (' not in text


def test_panel_escalates_the_context_colour():
    panel = _configured()

    def colour(percent: float) -> set[str | None]:
        panel._context_state = ContextState(percent=percent, used=int(percent * 2000), window=200_000)
        return {span.style for span in panel._build().spans}

    assert 'green' in colour(41.0)
    assert 'yellow' in colour(75.0)
    assert 'bold red' in colour(93.0)


def test_panel_orders_context_between_the_model_and_the_skills():
    panel = _configured()
    panel._skills = ('clean-code',)
    panel._context_state = ContextState(percent=66.0, used=132_000, window=200_000)
    text = panel._build().plain
    assert text.index('Model') < text.index('Context') < text.index('Loaded skills')


def test_panel_orders_the_workspace_between_the_model_and_the_context():
    panel = _configured()
    panel._workspace_state = WorkspaceState(label='main', branch='main')
    panel._context_state = ContextState(percent=66.0, used=132_000, window=200_000)
    text = panel._build().plain
    assert text.index('Model') < text.index('Workspace') < text.index('Context')


def test_panel_shows_the_workspace_label_and_branch():
    panel = _panel()
    panel._workspace_state = WorkspaceState(label='fix-login', branch='sigil/fix-login', is_worktree=True)
    text = panel._build().plain
    assert 'Workspace' in text
    assert 'fix-login' in text
    assert 'sigil/fix-login' in text


def test_panel_hides_the_workspace_until_it_is_known():
    assert 'Workspace' not in _panel()._build().plain
    panel = _panel()
    panel._workspace_state = WorkspaceState()
    assert 'Workspace' not in panel._build().plain


def test_status_bar_paints_nothing():
    bar = StatusBar()
    assert bar.content.plain == ''


def test_panel_lists_loaded_skills():
    panel = _panel()
    panel._skills = ('clean-code', 'vscode')
    text = panel._build().plain
    assert 'clean-code' in text
    assert 'vscode' in text


def test_panel_hides_mcp_section_without_servers():
    panel = _panel()
    panel._mcp_known = True
    assert 'MCP' not in panel._build().plain


def test_panel_shows_count_inline_with_title():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (
        MCPServerState(name='github', ok=True),
        MCPServerState(name='playwright', ok=True),
    )
    text = panel._build().plain
    assert 'MCP (2)' in text
    assert 'github' in text
    assert 'playwright' in text


def test_panel_marks_failures_with_a_cross():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (
        MCPServerState(name='github', ok=True),
        MCPServerState(name='broken', ok=False),
    )
    text = panel._build().plain
    assert 'MCP (1, 1 ✗)' in text
    assert ' ✗ broken' in text
    assert ' ● github' in text


def test_panel_lists_only_failures_when_none_ok():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (MCPServerState(name='broken', ok=False),)
    text = panel._build().plain
    assert 'MCP (0, 1 ✗)' in text
    assert '●' not in text


def test_panel_colors_ok_green_and_failures_red():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (
        MCPServerState(name='github', ok=True),
        MCPServerState(name='broken', ok=False),
    )
    styles = {span.style for span in panel._build().spans}
    assert 'green' in styles
    assert 'bold red' in styles


def test_mcp_counts_ignores_disabled_servers(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(
        '{"mcp_servers": {"on": {"command": "npx"}, "off": {"command": "npx", "enabled": false}}}',
        encoding='utf-8',
    )
    manager = MCPManager(load_config([config], environ={}).servers)
    ok, failed = manager.counts()
    assert ok == 1
    assert failed == 0


def test_mcp_counts_reports_broken_servers(tmp_path):
    config = tmp_path / 'config.json'
    # An invalid transport makes the toolset construction fail.
    config.write_text('{"mcp_servers": {"broken": {"url": "not-a-url"}}}', encoding='utf-8')
    manager = MCPManager(load_config([config], environ={}).servers)
    ok, failed = manager.counts()
    assert ok == 0
    assert failed == 1


def test_sidebar_reflects_loaded_skills():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            loaded = LoadedSkills()
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = SigilApp(agent=agent, store=store, session=session, loaded_skills=loaded)

            async with app.run_test() as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)
                assert 'none yet' in panel._build().plain

                loaded.add('clean-code')
                app.refresh_sidebar()
                await pilot.pause()
                assert 'clean-code' in panel._build().plain

    asyncio.run(inner())
