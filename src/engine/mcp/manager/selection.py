"""Selection of the servers a query operates on."""

from __future__ import annotations

from collections.abc import Mapping

from src.engine.mcp.config_models.mcp_config_error import MCPConfigError
from src.engine.mcp.server.mcp_server import MCPServer

ServerMap = Mapping[str, MCPServer]


def require(servers: ServerMap, name: str) -> MCPServer:
    """Server by name, validating that it exists and is available."""
    server = servers.get(name)
    if server is None:
        available = ', '.join(sorted(servers)) or 'none'
        raise MCPConfigError(f"Unknown MCP server '{name}'. Configured servers: {available}.")
    if server.toolset is None:
        state = 'disabled' if not server.enabled else 'not available'
        raise MCPConfigError(f"MCP server '{name}' is {state}.")
    return server


def targets(servers: ServerMap, server: str | None) -> list[MCPServer]:
    """Servers reached by a query: a single one or every active one."""
    if server is not None:
        return [require(servers, server)]
    return [item for item in servers.values() if item.toolset is not None]
