"""The CompactionState value of the context layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CompactionState:
    """What the model view must no longer carry, plus the summary that replaces it.

    Identities are content-free: a part key or a tool call id, never the payload. That
    is what lets a resume, a rollback and a fork rebuild the same projection from the
    original transcript without storing a decision per message.
    """

    dropped_parts: tuple[str, ...] = ()
    """Reasoning parts removed from the view, by stable part key."""

    compacted_calls: tuple[str, ...] = ()
    """Tool calls whose arguments and result are sent in their compacted form."""

    summary: str | None = None
    """The checkpoint that replaces everything before ``summary_boundary``."""

    summary_boundary: str | None = None
    """Part key of the first message the summary does not replace."""

    image_removals: tuple[tuple[str, str], ...] = ()
    """Stable image occurrence keys and the reason each left the view."""

    evaluated_steps: int = 0

    def as_row(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> CompactionState:
        return cls(
            dropped_parts=tuple(row.get('dropped_parts', ())),
            compacted_calls=tuple(row.get('compacted_calls', ())),
            summary=row.get('summary') or None,
            summary_boundary=row.get('summary_boundary') or None,
            image_removals=tuple((key, reason) for key, reason in row.get('image_removals', ())),
            evaluated_steps=int(row.get('evaluated_steps', 0)),
        )
