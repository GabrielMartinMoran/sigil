"""The DebugTurn value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

from src.core.context.compaction.compaction_event import CompactionEvent
from src.core.context.meter.request_cache_row import RequestCacheRow

DebugEvent = Annotated[CompactionEvent, PlainSerializer(CompactionEvent.as_row)]


@dataclass(frozen=True, slots=True)
class DebugTurn:
    index: int
    last_input: int | None
    input_total: int
    cache_read: int
    unmeasured: int
    measured: int
    cache_ratio: float | None
    events: tuple[DebugEvent, ...] = ()
    """The compaction boundaries that fell inside this turn."""
    steps: tuple[RequestCacheRow | None, ...] = ()
    steps_before: int = 0
    step_events: tuple[tuple[DebugEvent, ...], ...] = ()
    step_costs: tuple[Decimal | None, ...] = ()
    """Available charges, with estimated values identified separately."""
    step_cost_estimated: tuple[bool, ...] = ()
    step_timestamps: tuple[datetime | None, ...] = ()

    @property
    def context_used(self) -> int | None:
        """The prompt size of the turn's last request: what the turn cost in context."""
        return self.last_input
