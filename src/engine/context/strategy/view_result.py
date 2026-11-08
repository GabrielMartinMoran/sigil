"""Result of projecting the log onto the model view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelMessage

if TYPE_CHECKING:
    from src.engine.context.compaction.coordinator import CompactionPlan
    from src.engine.context.compaction.events import CompactionEvent


@dataclass(frozen=True, slots=True)
class ViewResult:
    """Result of projecting the log onto the model view."""

    messages: list[ModelMessage]
    estimated_tokens: int
    dropped_messages: int = 0
    dropped_thinking: int = 0
    truncated_results: int = 0
    """Tool arguments and results reduced to a level of detail."""

    compacted_prompts: int = 0
    dropped_images: int = 0
    plan: CompactionPlan | None = None
    diagnostics: dict[str, object] | None = None
    budget_exceeded: bool = False
    events: tuple[CompactionEvent, ...] = ()
    """Recorded historical decisions followed by explicitly projected next-request events."""

    @property
    def last_event(self) -> CompactionEvent | None:
        """The most recent boundary, which is what the next request sits after."""
        return self.events[-1] if self.events else None

    @property
    def changed(self) -> bool:
        return bool(
            self.dropped_messages
            or self.dropped_thinking
            or self.truncated_results
            or self.compacted_prompts
            or self.dropped_images
        )
