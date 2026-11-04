"""Registro de comandos de la barra de entrada."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    description: str


COMMANDS: tuple[Command, ...] = (
    Command('/new', 'Iniciar una sesión nueva'),
    Command('/load', 'Cargar una sesión guardada'),
    Command('/mcp', 'Ver el estado de los servidores MCP'),
    Command('/help', 'Mostrar la ayuda de comandos'),
    Command('/quit', 'Salir de Sigil'),
)


def match_commands(text: str) -> list[Command]:
    """Comandos cuyo nombre empieza con lo tipeado (sólo si arranca con '/')."""
    if not text.startswith('/'):
        return []
    token = text.split(' ', 1)[0].lower()
    return [command for command in COMMANDS if command.name.startswith(token)]
