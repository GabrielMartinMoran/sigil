"""Result of projecting the log onto the model view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelMessage

if TYPE_CHECKING:
    from src.engine.context.compaction.coordinator import CompactionPlan
    from src.engine.context.compaction.events import CompactionEvent
    from src.engine.context.compaction.summary import SummaryRequest


@dataclass(frozen=True, slots=True)
class ViewResult:
    """Result of projecting the log onto the model view."""

    messages: list[ModelMessage]
    estimated_tokens: int
    dropped_thinking: int = 0
    compacted_parts: int = 0
    """Tool arguments and results sent in their compacted form."""
    dropped_images: int = 0
    plan: CompactionPlan | None = None
    diagnostics: dict[str, object] | None = None
    budget_exceeded: bool = False
    events: tuple[CompactionEvent, ...] = ()
    """Recorded historical decisions followed by explicitly projected next-request events."""
    summary_request: SummaryRequest | None = None
    """The span a model-written checkpoint should replace, when one is still needed."""

    summary_error: str | None = None
    """Content-free failure type for this projection, isolated from other runs."""

    @property
    def last_event(self) -> CompactionEvent | None:
        """The most recent boundary, which is what the next request sits after."""
        return self.events[-1] if self.events else None

    @property
    def changed(self) -> bool:
        return bool(
            self.dropped_thinking
            or self.compacted_parts
            or self.dropped_images
            or (self.plan is not None and self.plan.state.summary)
        )
