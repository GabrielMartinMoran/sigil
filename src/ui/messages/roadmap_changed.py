"""RoadmapChanged message of the terminal."""

from __future__ import annotations

from textual.message import Message


class RoadmapChanged(Message):
    """The roadmap store changed and the panel has to repaint."""
