"""Help text, grouped by who can serve the command."""

from __future__ import annotations

from src.engine.commands import Command, CommandOwner, owned_by
from src.ui.command_transport import listing_mark


def description(command: Command, *, attached: bool) -> str:
    """The words a listing prints for ``command``, and the mark when it cannot act here.

    One function for both listings — `/help` and the `/` menu — so a row cannot be
    worded one way in the help and another where it is picked. The words themselves
    are the catalogue's and say the same thing in either mode; the mark comes from
    `command_transport`, which is the only place that knows this terminal's wiring.
    """
    return f'{command.description}{listing_mark(command.name, attached=attached)}'


def _section(title: str, owner: CommandOwner, *, attached: bool) -> str:
    lines = [title]
    for command in owned_by(owner):
        lines.append(f'  {command.name:<22} {description(command, attached=attached)}')
    return '\n'.join(lines)


def help_text(*, attached: bool) -> str:
    """The two groups, with each row carrying the table's mark when it cannot act here.

    Two groups and not one list: they answer different questions. The first is what
    Sigil knows and can do — the same in every client. The second is how this window
    behaves, and a different client would have its own. One list hid that, and the
    hiding is what made "some of these are UI and some are core" hard to see.

    ``attached`` is the only thing this build adds to the catalogue's own words, and it
    is a fact about *this* window: a terminal that owns the engine runs every row, so
    nothing is marked there.
    """
    return '\n\n'.join(
        (
            _section('Session', CommandOwner.CORE, attached=attached),
            _section('This client', CommandOwner.CLIENT, attached=attached),
        )
    )


HELP_TEXT = help_text(attached=False)
"""The in-process rendering, and the plain names every listing keeps.

A terminal that owns the engine runs every row, so no row is marked. The attached
window asks `help_text(attached=True)` and gets the same sentences plus the marks.
"""

KEYS_TEXT = (
    'Keys: Enter sends · Shift+Enter adds a line · ctrl+v pastes an image · '
    'Up/Down pick in the menu · Tab completes · Esc closes the menu · '
    'pgup/pgdn and ctrl+↑/↓ read the conversation · '
    'ctrl+p command palette · ctrl+t the agents of the session · alt+d the agent debug view · '
    '/tools expanded shows the tool output'
)

__all__ = ['HELP_TEXT', 'KEYS_TEXT', 'description', 'help_text']
