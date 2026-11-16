"""The InstructionRow value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstructionRow:
    """An instruction block as it was sent."""

    name: str
    dynamic: bool
    """Whether the content is recomputed on every request (and can therefore break the prefix)."""
    tokens: int
    preview: str
