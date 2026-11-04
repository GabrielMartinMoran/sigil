"""Help text for the slash commands."""

from __future__ import annotations

from src.engine.commands import COMMANDS

HELP_TEXT = 'Commands: ' + ', '.join(f'{command.name} — {command.description}' for command in COMMANDS)
