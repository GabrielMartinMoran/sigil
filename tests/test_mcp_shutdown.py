"""Tests for the bounded shutdown of the MCP servers.

The regression these cover: closing Sigil waited out the SDK's two-second grace
period once per stdio server, because the probe leaves a kept-alive session open
and a server that ignores the EOF never exits on its own. The quit took seconds,
and every test of the app that built a manager paid for it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.engine.mcp import shutdown
from src.engine.mcp.config.loader import load_config
from src.engine.mcp.manager.manager import MCPManager


def stdio_server(tmp_path: Path) -> dict:
    """A real stdio MCP server, kept alive so the probe leaves a session open."""
    script = tmp_path / 'server.py'
    script.write_text(_SERVER_SCRIPT, encoding='utf-8')
    return {
        'command': __import__('sys').executable,
        'args': [str(script)],
        'keep_alive': True,
        'init_timeout': 30,
    }


_SERVER_SCRIPT = '''\
from mcp.server.mcpserver import MCPServer

mcp = MCPServer('probe')


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == '__main__':
    mcp.run()
'''


# --- the setting -------------------------------------------------------------


def test_the_default_grace_period_is_short():
    """Two seconds per server is the SDK's default and is far too long for a quit."""
    assert shutdown.read_shutdown_timeout({}) < 1.0


def test_the_grace_period_is_read_from_the_environment():
    environ = {shutdown.ENV_MCP_SHUTDOWN_TIMEOUT: '0.75'}
    assert shutdown.read_shutdown_timeout(environ) == pytest.approx(0.75)


@pytest.mark.parametrize('raw', ['', '   ', 'not-a-number', '0', '-1'])
def test_a_malformed_grace_period_falls_back_to_the_default(raw: str):
    """It tunes how long a quit waits; it is not worth refusing to start over."""
    environ = {shutdown.ENV_MCP_SHUTDOWN_TIMEOUT: raw}
    assert shutdown.read_shutdown_timeout(environ) == shutdown.DEFAULT_SHUTDOWN_TIMEOUT


def test_applying_the_setting_reaches_the_sdk():
    """The SDK reads its constant at call time, so assigning it is what takes effect."""
    from mcp.client import stdio

    original = stdio.PROCESS_TERMINATION_TIMEOUT
    try:
        applied = shutdown.apply_shutdown_timeout(0.4)
        assert applied == pytest.approx(0.4)
        assert stdio.PROCESS_TERMINATION_TIMEOUT == pytest.approx(0.4)
    finally:
        stdio.PROCESS_TERMINATION_TIMEOUT = original


def test_the_builder_installs_the_timeout_it_was_given():
    """The value travels from the composition root down to the SDK, not around it."""
    from mcp.client import stdio

    from src.engine.mcp.deferred import build_manager

    original = stdio.PROCESS_TERMINATION_TIMEOUT
    try:
        build_manager({}, None, shutdown_timeout=0.35)
        assert stdio.PROCESS_TERMINATION_TIMEOUT == pytest.approx(0.35)
    finally:
        stdio.PROCESS_TERMINATION_TIMEOUT = original


# --- the manager -------------------------------------------------------------


def test_closing_the_manager_releases_every_server(tmp_path):
    """`aclose` closes each transport and leaves no session running or process behind."""
    config = tmp_path / 'config.json'
    config.write_text(__import__('json').dumps({'mcp_servers': {'probe': stdio_server(tmp_path)}}), encoding='utf-8')
    manager = MCPManager(load_config([config], environ={}).servers)

    async def inner() -> None:
        await manager.describe(connect=True)
        server = manager.get('probe')
        assert server is not None
        # The default prefix is the server name: the model sees where a tool is from.
        assert server.tools == ('probe_add',)
        await manager.aclose()
        # The transport is closed: its session is gone and a new request would have
        # to reconnect, which is what "nothing left running" means here.
        assert server.toolset is not None
        assert server.toolset.is_running is False

    asyncio.run(inner())


def test_closing_the_manager_twice_is_harmless(tmp_path):
    """A second close happens on every path that shuts down defensively."""
    config = tmp_path / 'config.json'
    config.write_text(__import__('json').dumps({'mcp_servers': {'probe': stdio_server(tmp_path)}}), encoding='utf-8')
    manager = MCPManager(load_config([config], environ={}).servers)

    async def inner() -> None:
        await manager.describe(connect=True)
        await manager.aclose()
        await manager.aclose()

    asyncio.run(inner())


def test_closing_does_not_wait_the_full_grace_period_per_server(tmp_path):
    """The whole point: with several servers, the closes run concurrently.

    Sequentially, four servers that ignore the EOF cost four grace periods.
    """
    shutdown.apply_shutdown_timeout(0.3)
    servers = {}
    for index in range(4):
        directory = tmp_path / f's{index}'
        directory.mkdir()
        servers[f'probe{index}'] = stdio_server(directory)

    config = tmp_path / 'config.json'
    config.write_text(__import__('json').dumps({'mcp_servers': servers}), encoding='utf-8')
    manager = MCPManager(load_config([config], environ={}).servers)

    async def inner() -> float:
        await manager.describe(connect=True)
        started = time.perf_counter()
        await manager.aclose()
        return time.perf_counter() - started

    elapsed = asyncio.run(inner())
    assert elapsed < 1.0, f'closing four servers took {elapsed:.2f}s: the closes are not concurrent'


def test_a_manager_without_servers_closes_cleanly():
    manager = MCPManager({})

    async def inner() -> None:
        await manager.aclose()

    asyncio.run(inner())
