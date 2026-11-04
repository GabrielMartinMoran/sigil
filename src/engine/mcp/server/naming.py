"""Names and filters for the tools of an MCP server."""

from __future__ import annotations

from src.engine.mcp.config_models.server_spec import MCPServerSpec


def display_name(prefix: str, tool_name: str) -> str:
    """Tool name as the model sees it."""
    return f'{prefix}_{tool_name}' if prefix else tool_name


def includes(spec: MCPServerSpec, tool_name: str) -> bool:
    """Whether the tool passes the server's allowlist/denylist."""
    include = spec.config.include_tools
    if include:
        return tool_name in include
    return tool_name not in spec.config.exclude_tools
