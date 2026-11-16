"""The DebugPart value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DebugPart:
    """One part of a message, with its cost and a bounded preview."""

    kind: str
    label: str
    tokens: int
    chars: int
    preview: str
    unknown_tokens: bool = False
    clipped_chars: int = 0
    """Characters not shown because of the preview clipping."""

    @property
    def clipped(self) -> bool:
        return self.clipped_chars > 0
