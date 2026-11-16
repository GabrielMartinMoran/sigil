"""The block that opens an agent message."""

from __future__ import annotations

from textual.widgets import Static


class AgentMessageBlock(Static):
    """An instruction delivered by another agent, not a human message."""
