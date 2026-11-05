"""Help text for the slash commands."""

from __future__ import annotations

from src.engine.commands import COMMANDS

HELP_TEXT = 'Commands: ' + ', '.join(f'{command.name} — {command.description}' for command in COMMANDS)

KEYS_TEXT = (
    'Keys: Enter sends · Shift+Enter adds a line · Up/Down pick in the menu · Tab completes · Esc closes the menu'
)
