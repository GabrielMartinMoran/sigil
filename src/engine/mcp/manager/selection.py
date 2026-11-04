"""Selección de servidores sobre los que opera una consulta."""

from __future__ import annotations

from collections.abc import Mapping

from src.engine.mcp.config_models.mcp_config_error import MCPConfigError
from src.engine.mcp.server.mcp_server import MCPServer

ServerMap = Mapping[str, MCPServer]


def require(servers: ServerMap, name: str) -> MCPServer:
    """Servidor por nombre, validando que exista y esté disponible."""
    server = servers.get(name)
    if server is None:
        available = ', '.join(sorted(servers)) or 'none'
        raise MCPConfigError(f"Unknown MCP server '{name}'. Configured servers: {available}.")
    if server.toolset is None:
        state = 'disabled' if not server.enabled else 'not available'
        raise MCPConfigError(f"MCP server '{name}' is {state}.")
    return server


def targets(servers: ServerMap, server: str | None) -> list[MCPServer]:
    """Servidores alcanzados por una consulta: uno puntual o todos los activos."""
    if server is not None:
        return [require(servers, server)]
    return [item for item in servers.values() if item.toolset is not None]
