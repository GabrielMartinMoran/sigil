"""Tests del panel superior del sidebar: skills cargadas y conteo de MCP."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

os.environ.setdefault('LLM_MODEL', 'test/model')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.mcp import MCPManager, load_config
from src.engine.sessions.session_store import SessionStore
from src.engine.skills.loaded import LoadedSkills
from src.ui.app import SigilApp
from src.ui.widgets.sidebar_panel import MCPServerState, SidebarPanel


def _panel() -> SidebarPanel:
    return SidebarPanel()


def test_panel_without_skills_says_so():
    text = _panel()._build().plain
    assert 'Loaded skills' in text
    assert 'none yet' in text


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
    # Un transporte inválido hace fallar la construcción del toolset.
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
                app._refresh_sidebar()
                await pilot.pause()
                assert 'clean-code' in panel._build().plain

    asyncio.run(inner())
