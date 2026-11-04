"""Nombres y filtros de las tools de un servidor MCP."""

from __future__ import annotations

from src.engine.mcp.config_models.server_spec import MCPServerSpec


def display_name(prefix: str, tool_name: str) -> str:
    """Nombre de la tool tal como lo ve el modelo."""
    return f'{prefix}_{tool_name}' if prefix else tool_name


def includes(spec: MCPServerSpec, tool_name: str) -> bool:
    """Si la tool pasa el allowlist/denylist del servidor."""
    include = spec.config.include_tools
    if include:
        return tool_name in include
    return tool_name not in spec.config.exclude_tools
