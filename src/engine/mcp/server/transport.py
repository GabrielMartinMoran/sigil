"""Transports and construction of a server's ``MCPToolset``."""

from __future__ import annotations

from typing import Any

from pydantic_ai.mcp import MCPToolset, SSETransport, StdioTransport

from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.server.tool_hook import tool_hook
from src.engine.shell.guard import SELF_GUARD_ENV
from src.engine.tooling.logging import ToolLogger


def stdio_transport(spec: MCPServerSpec) -> StdioTransport:
    """stdio transport, with the environment guard on the child process."""
    config = spec.config
    cwd = spec.cwd_path
    return StdioTransport(
        command=config.command or '',
        args=list(config.args),
        # The environment guard prevents a server configured with
        # `command: "sigil"` from spawning another agent instance.
        env={**config.env, SELF_GUARD_ENV: '1'},
        cwd=str(cwd) if cwd is not None else None,
        keep_alive=config.keep_alive,
    )


def create_toolset(spec: MCPServerSpec, logger: ToolLogger | None) -> MCPToolset:
    """Creates the server's ``MCPToolset``, without filters or prefix."""
    config = spec.config
    options: dict[str, Any] = {
        'id': spec.name,
        'include_instructions': config.include_instructions,
        'init_timeout': config.init_timeout,
        'read_timeout': config.read_timeout,
        'process_tool_call': tool_hook(spec, logger),
    }

    if spec.transport_kind == 'stdio':
        return MCPToolset(stdio_transport(spec), **options)

    url = config.url or ''
    headers = dict(config.headers) or None
    if config.transport == 'sse':
        return MCPToolset(SSETransport(url, headers=headers), **options)
    return MCPToolset(url, headers=headers, **options)
