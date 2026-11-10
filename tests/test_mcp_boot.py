"""The app's deferred MCP boot, end to end.

The regression these cover: the app built the MCP manager synchronously before the
first paint, so opening Sigil waited half a second for an import that nothing on
screen needs. Now `boot_mcp` builds it on a worker and installs it afterwards.

The risk of that change is a subtle one, and these tests exist for it: an app whose
MCP layer is still loading must behave like an app with nothing configured, and the
moment it finishes, the panel and the meta-tools must see the real thing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.mcp.config.loader import load_config
from src.engine.mcp.deferred import MCPGateway, build_manager
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.sidebar_panel import SidebarPanel
from tests.ui_helpers import wait_until

SERVER_SCRIPT = '''\
from mcp.server.mcpserver import MCPServer

mcp = MCPServer('probe')


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == '__main__':
    mcp.run()
'''


def _config(tmp_path: Path, *, keep_alive: bool = False) -> Path:
    script = tmp_path / 'server.py'
    script.write_text(SERVER_SCRIPT, encoding='utf-8')
    config = tmp_path / 'config.json'
    config.write_text(
        json.dumps(
            {
                'mcp_servers': {
                    'probe': {
                        'command': sys.executable,
                        'args': [str(script)],
                        'keep_alive': keep_alive,
                        'init_timeout': 30,
                    }
                }
            }
        ),
        encoding='utf-8',
    )
    return config


def test_the_agent_starts_with_the_placeholder_and_the_meta_tools(tmp_path: Path):
    """The meta-tools exist from the first paint and answer through the gateway.

    This is what makes the deferral safe: `/mcp`, resources and prompts do not
    need the manager to be built, only to be reachable when they are used.
    """
    specs = load_config([_config(tmp_path)], environ={}).servers
    gateway = MCPGateway(configured=len(specs))

    agent = Agent(TestModel(custom_output_text='ok'), toolsets=[gateway.pending])

    tools = agent._function_toolset
    assert tools is not None
    # Nothing MCP is in the function toolset: the meta-tools are registered by the
    # composition root, and the placeholder contributes no server tools yet.
    assert gateway.pending.loaded is False
    assert gateway.toolsets == []


def test_the_app_boots_mcp_and_the_panel_ends_up_with_the_real_state(tmp_path: Path):
    """The worker runs, installs the layer, and the panel shows the server."""
    config = _config(tmp_path)
    specs = load_config([config], environ={}).servers
    gateway = MCPGateway(configured=len(specs))

    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    app = SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        mcp_manager=gateway,
        mcp_loader=lambda: build_manager(specs, None),
    )

    async def inner() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await wait_until(pilot, lambda: app.query_one(SidebarPanel)._mcp_known, timeout=10)
            assert gateway.loaded is True
            assert gateway.pending.loaded is True
            text = app.query_one(SidebarPanel).text
            assert 'MCP (1)' in text
            assert 'probe' in text
            await gateway.aclose()

    asyncio.run(inner())


def test_an_app_with_nothing_configured_never_starts_the_worker(tmp_path: Path):
    """No servers means no load, no probe and no MCP section, as before."""
    gateway = MCPGateway(configured=0)
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    app = SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        mcp_manager=gateway,
        mcp_loader=lambda: (_ for _ in ()).throw(AssertionError('loader must not run')),
    )

    async def inner() -> None:
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            assert gateway.loaded is False
            # The section is absent, not empty.
            assert 'MCP' not in app.query_one(SidebarPanel).text

    asyncio.run(inner())


def test_a_failing_load_is_reported_in_the_chat_and_does_not_break_the_app(tmp_path: Path):
    """A broken MCP layer must not take the window down with it."""
    gateway = MCPGateway(configured=1)
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    app = SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        mcp_manager=gateway,
        mcp_loader=lambda: (_ for _ in ()).throw(RuntimeError('boom')),
    )

    async def inner() -> None:
        async with app.run_test(size=(120, 40)):
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
            assert gateway.loaded is False
            assert any('boom' in error for error in gateway.errors)
            # The window is alive and the app still answers.
            assert app.is_running

    asyncio.run(inner())
