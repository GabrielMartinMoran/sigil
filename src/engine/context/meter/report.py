"""Full breakdown of the context of a session."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.context.meter.snapshot import ContextSnapshot
from src.engine.context.meter.usage import ToolResultUsage, TurnUsage
from src.engine.context.strategy.view_result import ViewResult


@dataclass(frozen=True, slots=True)
class ContextReport:
    """Full breakdown for `/context`."""

    snapshot: ContextSnapshot
    instructions: int
    tools: int
    history: int
    turns: list[TurnUsage] = field(default_factory=list)
    heaviest_tool_results: list[ToolResultUsage] = field(default_factory=list)
    view: ViewResult | None = None
