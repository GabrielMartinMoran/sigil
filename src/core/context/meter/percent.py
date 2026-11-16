"""The percent helper of the context layer."""

from __future__ import annotations


def percent(used: int | None, window: int | None) -> float | None:
    """Percentage of the window used, or ``None`` if information is missing."""
    if not window or used is None:
        return None
    return max(0.0, used / window * 100.0)
