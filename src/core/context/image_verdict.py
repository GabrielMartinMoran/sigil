"""The ImageVerdict value of the context layer."""

from __future__ import annotations

from enum import StrEnum


class ImageVerdict(StrEnum):
    """What happens to one image occurrence in the view."""

    KEEP = 'keep'
    SUPERSEDED = 'superseded'
    """A newer copy of the same picture is attached further on."""
    EXPIRED = 'expired'
    """Its turn is older than the window."""
    OVER_BUDGET = 'over_budget'
    """The pixel cap was spent before reaching it."""

    @property
    def travels(self) -> bool:
        return self is ImageVerdict.KEEP
