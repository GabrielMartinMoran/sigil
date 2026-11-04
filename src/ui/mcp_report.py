"""Formato de texto del estado de los servidores MCP para el chat."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.mcp.models import ServerStatus


def server_state(status: ServerStatus) -> str:
    """Etiqueta corta del estado de un servidor."""
    if not status.enabled:
        return 'disabled'
    if status.error:
        return 'error'
    return 'ok'


def format_mcp_status(statuses: Sequence[ServerStatus]) -> str:
    """Reporte legible del estado de todos los servidores configurados."""
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
