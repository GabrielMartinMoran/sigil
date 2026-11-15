"""Full breakdown of the context of a session."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.context.meter.cost import SessionCost
from src.core.context.meter.snapshot import ContextSnapshot
from src.core.context.meter.usage import PromptUsage, ToolResultUsage
from src.core.context.strategy.view_result import ViewResult


@dataclass(frozen=True, slots=True)
class ContextReport:
    """Full breakdown for `/context`."""

    snapshot: ContextSnapshot
    instructions: int
    tools: int
    history: int
    cost: SessionCost = field(default_factory=SessionCost)
    turns: list[PromptUsage] = field(default_factory=list)
    heaviest_tool_results: list[ToolResultUsage] = field(default_factory=list)
    view: ViewResult | None = None
