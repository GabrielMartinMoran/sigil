"""The DebugMessage value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.context.meter.debug_part import DebugPart


@dataclass(frozen=True, slots=True)
class DebugMessage:
    """A message with its parts, measured in tokens."""

    index: int
    role: str
    """``request`` (what goes into the model) or ``response`` (what the model produced)."""
    tokens: int
    parts: tuple[DebugPart, ...] = ()
    instructions: tuple[str, ...] = ()
    """Names of the instruction blocks that travel with this request."""

    @property
    def kinds(self) -> str:
        """One-line summary: ``user · tool_return ×3``."""
        if not self.parts:
            return '(empty)'
        counts: dict[str, int] = {}
        for part in self.parts:
            counts[part.kind] = counts.get(part.kind, 0) + 1
        return ' · '.join(f'{kind} ×{count}' if count > 1 else kind for kind, count in counts.items())
