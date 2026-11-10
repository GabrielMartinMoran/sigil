"""The catalogue of input-bar commands, and who can serve each one.

A command palette belongs to a client, but the *effect* of a command does not always
live there. `/new` creates a session and `/rollback` destroys a turn — a web client must
be able to do both, and must not have to reimplement them. `/theme` is the opposite: it
repaints this terminal and means nothing anywhere else.

So every command declares its **owner**, and the test for it is the same one the
architecture page uses: **if it decides, it is core; if it shows, it is client.**

- ``CORE`` — the session's state or the answer comes from the domain: create, load,
  choose the agent, the account, the model, the effort; go back to a message; report the
  context or the MCP servers. A second client keeps these and formats the answer its own
  way.
- ``CLIENT`` — it only makes sense inside one client: the theme, the sidebar, whether the
  reasoning is collapsed, the debug view, the help, quitting. A second client brings its
  own, and none of them is part of the core's API.

The catalogue stays in the engine because it is shared vocabulary and carries no widget:
a client reads it, keeps every command, and knows which ones it must delegate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CommandOwner(StrEnum):
    """Who can serve a command."""

    CORE = 'core'
    """The domain owns the state and the answer. A client may format it, not redo it."""

    CLIENT = 'client'
    """Only the client in front of the user can: it configures or paints itself."""


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    owner: CommandOwner = CommandOwner.CORE


COMMANDS: tuple[Command, ...] = (
    # --- the core answers these -------------------------------------------------
    Command('/new', 'Start a new session; --worktree <name> creates a git worktree'),
    Command('/load', 'Load a saved session (of this or another worktree)'),
    Command('/agent', 'Choose the agent that sets Sigil’s behaviour'),
    Command('/provider', 'Manage provider accounts (add, remove, rename)'),
    Command('/model', 'Pick a model and reasoning effort across providers'),
    Command('/effort', 'Show or set the reasoning effort'),
    Command('/external-files', 'Show or set outside-workspace file access (allow/deny)'),
    Command('/mcp', 'Show MCP server status'),
    Command('/context', 'Show the context usage breakdown'),
    Command(
        '/rollback',
        'Go back to before a message: roll back (files included) or fork the conversation',
    ),
    # --- only this client can ---------------------------------------------------
    Command(
        '/reasoning-visibility',
        'Show or collapse the reasoning in the chat (on/off)',
        CommandOwner.CLIENT,
    ),
    Command(
        '/tools',
        'Show or collapse the tool output in the chat (expanded/collapsed)',
        CommandOwner.CLIENT,
    ),
    Command(
        '/theme',
        'Pick a theme with a live preview; --list shows the installed ones',
        CommandOwner.CLIENT,
    ),
    Command('/sidebar', 'Show or hide the sidebar (on/off)', CommandOwner.CLIENT),
    Command('/alerts', 'Show desktop alert status; on/off changes it', CommandOwner.CLIENT),
    Command(
        '/debug',
        'Toggle the agent debug view (alt+d); --text dumps the report',
        CommandOwner.CLIENT,
    ),
    Command('/help', 'Show command help', CommandOwner.CLIENT),
    Command('/quit', 'Quit Sigil', CommandOwner.CLIENT),
)


def match_commands(text: str) -> list[Command]:
    """Commands whose name starts with what was typed (only if it starts with '/')."""
    if not text.startswith('/'):
        return []
    token = text.split(' ', 1)[0].lower()
    return [command for command in COMMANDS if command.name.startswith(token)]


def owned_by(owner: CommandOwner) -> tuple[Command, ...]:
    """The commands one side can serve, in catalogue order."""
    return tuple(command for command in COMMANDS if command.owner is owner)


__all__ = ['COMMANDS', 'Command', 'CommandOwner', 'match_commands', 'owned_by']
