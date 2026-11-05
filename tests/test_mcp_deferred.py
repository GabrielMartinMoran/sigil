"""Tests for the deferred MCP load.

The regression these cover: building the MCP manager imports ``pydantic_ai.mcp``,
which drags in FastMCP and the MCP SDK and costs about half a second. Doing that
before the first paint is what made opening Sigil slow, and none of it is needed to
draw a window.

The contract is narrow on purpose: the agent starts with a placeholder that has no
tools, the real layer arrives later, and both the panel and the meta-tools keep
working without knowing the difference.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic_ai import Agent, Tool
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from src.engine.mcp.config.loader import load_config
from src.engine.mcp.deferred import MCPGateway
from src.engine.mcp.manager.manager import MCPManager
from src.engine.mcp.pending import PendingToolset


def _context(agent: Agent):
    """A run context, which is what `get_tools` validates its inputs against."""
    from pydantic_ai.tools import RunContext

    return RunContext(deps=None, model=TestModel(), usage=None, prompt=None, messages=[], run_id='test')


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


# --- the placeholder ---------------------------------------------------------


def test_the_placeholder_starts_empty_and_becomes_transparent():
    """Before the load it answers nothing; after it, it is the real toolset."""
    pending = PendingToolset()
    assert pending.loaded is False

    def late(x: int) -> int:
        """Late tool."""
        return x

    async def inner() -> None:
        assert pending._toolset is None

        pending.fill(FunctionToolset([Tool(late, takes_ctx=False)]))
        assert pending.loaded is True
        # `get_tools` needs a real run context; membership is enough to prove
        # the swap reached the agent's view.
        assert pending._toolset is not None

    asyncio.run(inner())


def test_calling_a_tool_before_the_load_raises_a_useful_error():
    """A model cannot reach a tool that does not exist; the message says why."""

    async def inner() -> None:
        with pytest.raises(LookupError, match='before its server was loaded'):
            await PendingToolset().call_tool('probe_add', {}, None, None)

    asyncio.run(inner())


def test_the_agent_sees_tools_that_arrive_after_it_was_built():
    """The whole point: the agent is constructed before the MCP layer exists.

    pydantic-ai asks the toolset for its tools on every step, so filling the
    placeholder later is visible without rebuilding the agent.
    """

    def late(x: int) -> int:
        """Late tool."""
        return x

    pending = PendingToolset()
    agent = Agent(TestModel(call_tools=['late']), toolsets=[pending])

    # A run before the load fails the way a missing tool fails, not the app.
    pending.fill(FunctionToolset([Tool(late, takes_ctx=False)]))
    result = asyncio.run(agent.run('go'))

    assert result.output == '{"late":0}'


# --- the gateway -------------------------------------------------------------


def test_the_gateway_is_falsy_only_when_nothing_is_configured():
    """`if not manager` is how the app decides whether to paint the section."""
    assert not MCPGateway(configured=0)
    assert MCPGateway(configured=2)


def test_the_gateway_waits_for_the_load_before_answering_about_servers(tmp_path: Path):
    """`describe` is the diagnostic path: it waits instead of lying with an empty list."""
    gateway = MCPGateway(configured=1)

    async def inner() -> None:
        waiter = asyncio.create_task(gateway.describe())
        await asyncio.sleep(0)
        assert not waiter.done(), 'describe answered before the layer was installed'

        manager = MCPManager({})
        gateway.install(manager)
        assert await waiter == []

    asyncio.run(inner())


def test_a_failed_load_releases_whoever_is_waiting():
    """An app whose MCP layer will never exist must not hang on `/mcp`."""
    gateway = MCPGateway(configured=1)

    async def inner() -> None:
        waiter = asyncio.create_task(gateway.describe())
        await asyncio.sleep(0)
        gateway.fail('boom')
        assert await waiter == []
        assert 'boom' in gateway.errors

    asyncio.run(inner())


def test_the_errors_are_a_flat_sequence_of_messages():
    """`errors` is iterated and joined by the callers, so it is flat and of strings.

    The regression: `errors` returned ``(self.config_errors, *load_errors)``, which
    nests the config errors in a tuple of their own. Every consumer iterates it as
    text — the startup notice, the chat line for each load error — and got a tuple
    instead of a message.
    """
    gateway = MCPGateway(configured=1, config_errors=('bad file', 'bad server'))
    gateway.fail('boom')

    assert gateway.errors == ('bad file', 'bad server', 'boom')
    assert all(isinstance(error, str) for error in gateway.errors)


def test_a_gateway_without_errors_reports_none():
    """The common case: no config problems and a clean load."""
    gateway = MCPGateway(configured=1)

    assert gateway.errors == ()

    async def inner() -> None:
        gateway.install(MCPManager({}))
        assert gateway.errors == ()

    asyncio.run(inner())


def test_closing_before_the_load_finishes_is_harmless():
    """Quitting in the first second happens; it must not raise."""
    gateway = MCPGateway(configured=3)

    async def inner() -> None:
        await gateway.aclose()

    asyncio.run(inner())


# --- with a real server ------------------------------------------------------


def test_installing_the_layer_makes_the_real_tools_available(tmp_path: Path):
    """End to end: placeholder in place, load finishes, the tool exists."""
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
                        'keep_alive': False,
                        'init_timeout': 30,
                    }
                }
            }
        ),
        encoding='utf-8',
    )
    specs = load_config([config], environ={}).servers
    gateway = MCPGateway(configured=len(specs))
    agent = Agent(TestModel(), toolsets=[gateway.pending])

    def late(x: int) -> int:
        """Late tool."""
        return x

    assert gateway.pending.loaded is False

    async def inner() -> None:
        manager = MCPManager(specs)
        gateway.install(manager)
        assert gateway.loaded is True
        assert len(gateway.toolsets) == 1
        # The agent holds the placeholder, and it now resolves to the real layer.
        held = agent.toolsets[1]
        assert isinstance(held, PendingToolset)
        assert held.loaded is True
        tools = await held._toolset.get_tools(_context(agent))
        assert 'probe_add' in tools
        await gateway.aclose()

    asyncio.run(inner())
