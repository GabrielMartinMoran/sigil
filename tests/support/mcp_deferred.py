"""Shared mcp deferred test support."""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel


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
