"""The DebugReport value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.context.meter.cache_summary import CacheSummary
from src.core.context.meter.context_snapshot import ContextSnapshot
from src.core.context.meter.debug_message import DebugMessage
from src.core.context.meter.debug_projection import DebugProjection
from src.core.context.meter.debug_turn import DebugTurn
from src.core.context.meter.request_snapshot import RequestSnapshot


@dataclass(frozen=True, slots=True)
class DebugReport:
    """Everything the debug view paints."""

    snapshot: ContextSnapshot
    request: RequestSnapshot
    cache: CacheSummary
    turns: tuple[DebugTurn, ...] = ()
    log: tuple[DebugMessage, ...] = ()
    view: tuple[DebugMessage, ...] = ()
    view_result: DebugProjection | None = None
    log_tokens: int = 0
    view_tokens: int = 0
    window: int | None = None
    effort: str | None = None
    model: str | None = None
    strategies_note: str = ''

    @property
    def dropped_tokens(self) -> int:
        """Log tokens the agent will not see on the next request."""
        return max(0, self.log_tokens - self.view_tokens)

    @property
    def reduction_percent(self) -> float | None:
        if not self.log_tokens:
            return None
        return self.dropped_tokens / self.log_tokens * 100.0

    @property
    def has_view(self) -> bool:
        return self.view_result is not None
