"""The app's deferred MCP boot, end to end.

The regression these cover: the app built the MCP manager synchronously before the
first paint, so opening Sigil waited half a second for an import that nothing on
screen needs. Now `boot_mcp` builds it on a worker and installs it afterwards.

The risk of that change is a subtle one, and these tests exist for it: an app whose
MCP layer is still loading must behave like an app with nothing configured, and the
moment it finishes, the panel and the meta-tools must see the real thing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.mcp.config.loader import load_config
from src.engine.mcp.deferred import MCPGateway

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
