"""Plain-text format of the MCP server state for the chat."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.mcp.tool_models.server_status import ServerStatus


def server_state(status: ServerStatus) -> str:
    """Short label of a server's state."""
    if not status.enabled:
        return 'disabled'
    if status.error:
        return 'error'
    return 'ok'


def format_mcp_status(statuses: Sequence[ServerStatus]) -> str:
    """Readable report of the state of every configured server."""
    if not statuses:
        return 'No MCP servers configured.'

    width = max(len(status.name) for status in statuses)
    pad = ' ' * width
    lines = [f'MCP servers ({len(statuses)})', '']

    for status in statuses:
        lines.append(f'  {status.name.ljust(width)}  {status.transport:<5}  {server_state(status)}')
        if status.tools:
            lines.append(f'  {pad}  tools: {", ".join(status.tools)}')
        if status.error:
            lines.append(f'  {pad}  error: {status.error}')
        lines.append(f'  {pad}  config: {status.source}')

    return '\n'.join(lines)


__all__ = ['format_mcp_status', 'server_state']
