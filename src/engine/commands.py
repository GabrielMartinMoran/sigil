"""Registro de comandos de la barra de entrada."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    description: str


COMMANDS: tuple[Command, ...] = (
    Command('/new', 'Start a new session'),
    Command('/load', 'Load a saved session'),
    Command('/sidebar', 'Show or hide the sidebar (on/off)'),
    Command('/mcp', 'Show MCP server status'),
    Command('/context', 'Show the context usage breakdown'),
    Command('/debug', 'Toggle the agent debug view (alt+d); --text dumps the report'),
    Command('/help', 'Show command help'),
    Command('/quit', 'Quit Sigil'),
)


def match_commands(text: str) -> list[Command]:
    """Comandos cuyo nombre empieza con lo tipeado (sólo si arranca con '/')."""
    if not text.startswith('/'):
        return []
    token = text.split(' ', 1)[0].lower()
    return [command for command in COMMANDS if command.name.startswith(token)]
