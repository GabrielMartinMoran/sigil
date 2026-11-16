"""One rollback point the screen offers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RollbackChoice:
    """What the user picked: a point, and what to do with it."""

    turn_id: str
    action: str
    """`rollback_files`, `rollback_only` or `fork`."""
