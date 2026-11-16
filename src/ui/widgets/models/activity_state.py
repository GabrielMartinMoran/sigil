"""Activity and turn state the status bar paints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActivityState:
    """Whether a turn is running and what it is doing right now."""

    running: bool = False
    label: str = ''
    elapsed: float = 0.0
