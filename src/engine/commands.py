"""Registry of the input bar commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    description: str


COMMANDS: tuple[Command, ...] = (
    Command('/new', 'Start a new session; --worktree <name> creates a git worktree'),
    Command('/load', 'Load a saved session (of this or another worktree)'),
    Command('/agent', 'Choose the agent that sets Sigil’s behaviour'),
    Command('/provider', 'Manage provider accounts (add, remove, activate)'),
    Command('/model', 'Pick the model for the active account'),
    Command('/effort', 'Show or set the reasoning effort'),
    Command('/reasoning-visibility', 'Show or collapse the reasoning in the chat (on/off)'),
    Command('/theme', 'Pick a theme with a live preview; --list shows the installed ones'),
    Command('/sidebar', 'Show or hide the sidebar (on/off)'),
    Command('/mcp', 'Show MCP server status'),
    Command('/context', 'Show the context usage breakdown'),
    Command(
        '/rollback',
        'Go back to before a message: roll back (files included) or fork the conversation',
    ),
    Command('/debug', 'Toggle the agent debug view (alt+d); --text dumps the report'),
    Command('/help', 'Show command help'),
    Command('/quit', 'Quit Sigil'),
)


def match_commands(text: str) -> list[Command]:
    """Commands whose name starts with what was typed (only if it starts with '/')."""
    if not text.startswith('/'):
        return []
    token = text.split(' ', 1)[0].lower()
    return [command for command in COMMANDS if command.name.startswith(token)]
