"""The name of the terminal window: which repository, and which conversation.

With several terminals open, "Sigil" in every one of them says nothing: the user
needs to know *where* the agent is working and *what* it is working on, without
switching to the window to find out. The title is therefore
``<repository> — <session title>``, and both halves matter: the repository names
the tree, the title names the conversation inside it.

The sequence is written straight to the terminal through the driver, the way the
clipboard is (``App.copy_to_clipboard``): Textual's console is backed by a null
file, so the Rich helper for window titles would write nowhere.

Nothing here raises. A terminal that ignores the sequence, a driver that is not
attached yet or a headless run must all keep working: the title is a convenience,
not a dependency of the session.
"""

from __future__ import annotations

from typing import Any

SEPARATOR = ' — '
"""An em dash with thin spaces, the same join the header uses for a subtitle."""

MAX_LENGTH = 120
"""Terminals truncate on their own, and not always gracefully. Cut here instead."""

OSC = '\x1b]2;{title}\x07'
"""`OSC 2` sets the window title on xterm, VTE, kitty, iTerm2, Windows Terminal and friends."""

_CONTROL_MAX = 0x20
_DEL = 0x7F
"""Control characters, plus DEL. Both are unsafe inside an escape sequence."""


def _plain(text: str) -> str:
    """``text`` with the whitespace collapsed and every control character gone.

    A title comes from a model, which is to say from an untrusted source. A stray
    ESC or BEL would not be printed: it would end the sequence early and hand the
    terminal the rest of the title as a command. It is dropped, not escaped.
    """
    collapsed = ' '.join(str(text or '').split())
    return ''.join(character for character in collapsed if ord(character) >= _CONTROL_MAX and ord(character) != _DEL)


def compose(repository: str, session_title: str, *, separator: str = SEPARATOR) -> str:
    """The window title of a session, from its repository and its own title.

    Each half is optional in practice: a directory that is not a repository has a
    folder name, and a session that has not been named yet has nothing. Whichever
    survives is shown alone rather than in front of a dangling separator.
    """
    parts = [_plain(repository), _plain(session_title)]
    title = separator.join(part for part in parts if part)
    return title[:MAX_LENGTH]


def apply(driver: Any, title: str) -> bool:
    """Sets the terminal window title. Returns whether it was written.

    It takes the driver and not the app so it can be called from anywhere and
    tested with a stand-in that records what it was asked to write.
    """
    if driver is None or not title:
        return False
    if getattr(driver, 'is_headless', False):
        return False
    try:
        driver.write(OSC.format(title=title))
    except Exception:  # noqa: BLE001 - a title never breaks a session
        return False
    return True
