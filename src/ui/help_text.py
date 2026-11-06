"""Help text, grouped by who can serve the command."""

from __future__ import annotations

from src.engine.commands import CommandOwner, owned_by


def _section(title: str, owner: CommandOwner) -> str:
    lines = [title]
    for command in owned_by(owner):
        lines.append(f'  {command.name:<22} {command.description}')
    return '\n'.join(lines)


HELP_TEXT = '\n\n'.join(
    (
        _section('Session', CommandOwner.CORE),
        _section('This client', CommandOwner.CLIENT),
    )
)
"""Two groups and not one list: they answer different questions.

The first is what Sigil knows and can do — the same in every client. The second is how
this window behaves, and a different client would have its own. One list hid that, and
the hiding is what made "some of these are UI and some are core" hard to see.
"""

KEYS_TEXT = (
    'Keys: Enter sends · Shift+Enter adds a line · ctrl+v pastes an image · '
    'Up/Down pick in the menu · Tab completes · Esc closes the menu · '
    'pgup/pgdn and ctrl+↑/↓ read the conversation · '
    'ctrl+t the agents of the session · alt+d the agent debug view · '
    '/tools expanded shows the tool output'
)

__all__ = ['HELP_TEXT', 'KEYS_TEXT']
