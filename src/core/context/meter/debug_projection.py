"""The DebugProjection value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.context.meter.debug_turn import DebugEvent


@dataclass(frozen=True, slots=True)
class DebugProjection:
    """Content-free projection metadata; never retain messages or summary text."""

    diagnostics: dict[str, object] | None = None
    budget_exceeded: bool = False
    summary_error: str | None = None
    events: tuple[DebugEvent, ...] = ()
    recorded_events: int = 0
