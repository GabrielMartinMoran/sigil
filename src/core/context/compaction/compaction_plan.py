"""The CompactionPlan value of the context layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.context.compaction.compaction_event import CompactionEvent
from src.core.context.compaction.compaction_state import CompactionState


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    state: CompactionState = field(default_factory=CompactionState)
    events: tuple[CompactionEvent, ...] = ()
    recorded_events: int = 0
    """How many of ``events`` came from the transcript instead of this projection."""
    exceptions: tuple[str, ...] = ()
    effective_policy: Mapping[str, Any] = field(default_factory=dict)
