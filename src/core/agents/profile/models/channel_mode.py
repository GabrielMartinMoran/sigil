"""The ChannelMode value of the agents layer."""

from __future__ import annotations

from enum import StrEnum


class ChannelMode(StrEnum):
    """How much of the conversation between two agents is allowed."""

    UNIDIRECTIONAL = 'unidirectional'
    """The parent talks to the subagent. Nothing goes the other way."""

    BIDIRECTIONAL = 'bidirectional'
    """The subagent can also send notices back, to its parent and its siblings."""
