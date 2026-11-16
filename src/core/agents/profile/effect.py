"""The Effect value of the agents layer."""

from __future__ import annotations

from enum import StrEnum


class Effect(StrEnum):
    """What a rule does when it matches."""

    ALLOW = 'allow'
    """The call (or the tool) is permitted."""

    DENY = 'deny'
    """The call (or the tool) is refused."""
