"""TransportState message of the terminal."""

from __future__ import annotations

from enum import StrEnum


class TransportState(StrEnum):
    """How this terminal can serve one command."""

    CLIENT = 'client'
    """It runs here: it paints or configures this window and travels nowhere."""

    DELEGATED = 'delegated'
    """The service owns it and this terminal has a sender for its route."""

    NOT_WIRED = 'not_wired'
    """The service owns it and has the route; this terminal has no sender yet."""

    BLOCKED = 'blocked'
    """The service owns it and has no endpoint for it, so nothing can be wired."""
