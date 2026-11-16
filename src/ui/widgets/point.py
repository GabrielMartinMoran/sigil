"""A point a session can be returned to."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.agents.checkpoint import Checkpoint


@dataclass(frozen=True, slots=True)
class Point:
    """One rollback point, as the screen shows it."""

    checkpoint: Checkpoint
    preview: str
    """The first line of the prompt that opened the turn."""
    files: int | None = None
    """How many files a rollback would touch, or `None` when it is unknown."""
