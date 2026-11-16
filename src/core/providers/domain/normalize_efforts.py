"""Filters and orders whatever the catalogue said about efforts."""

from __future__ import annotations

from src.core.providers.domain.thinking import EFFORT_LEVELS, ThinkingEffort


def normalize_efforts(raw: object) -> tuple[ThinkingEffort, ...]:
    """Filters and orders whatever the catalogue said about efforts.

    Unknown levels are dropped, duplicates collapse and the result follows
    ``EFFORT_LEVELS`` order so every list in the UI reads least -> most.
    """
    if not raw:
        return ()
    seen = {str(item).strip().lower() for item in raw}  # type: ignore[union-attr]
    return tuple(level for level in EFFORT_LEVELS if level in seen)
