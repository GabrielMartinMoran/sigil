from enum import StrEnum


class CommandOwner(StrEnum):
    """Who can serve a command."""

    CORE = 'core'
    """The domain owns the state and the answer. A client may format it, not redo it."""

    CLIENT = 'client'
    """Only the client in front of the user can: it configures or paints itself."""
