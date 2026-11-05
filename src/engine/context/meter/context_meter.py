"""Measuring the context of a session and building the budget."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import DEFAULT_OUTPUT_RESERVE, ContextBudget
from src.engine.context.meter.cost import session_cost
from src.engine.context.meter.report import ContextReport
from src.engine.context.meter.snapshot import ContextSnapshot
from src.engine.context.meter.usage import heaviest_tool_results, turn_usage
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens, measured_tokens, projected_tokens


class ContextMeter:
    """Measures the context of a session and builds the budget."""

    def __init__(self, *, window: int | None = None, output_reserve: int = DEFAULT_OUTPUT_RESERVE) -> None:
        self._window = window if window and window > 0 else None
        self._output_reserve = output_reserve
        self._instructions = 0
        self._tools = 0

    @property
    def window(self) -> int | None:
        return self._window

    def set_window(self, window: int | None) -> None:
        """Sets the window. A falsy value clears it, so the bar shows `?`."""
        self._window = window if window and window > 0 else None

    def set_overhead(self, *, instructions: int | None = None, tools: int | None = None) -> None:
        """Sets the cost of the blocks that are always sent."""
        if instructions is not None:
            self._instructions = instructions
        if tools is not None:
            self._tools = tools

    def budget(self) -> ContextBudget:
        return ContextBudget(
            window=self._window,
            tools=self._tools,
            instructions=self._instructions,
            output_reserve=self._output_reserve,
        )

    def snapshot(self, messages: Sequence[ModelMessage]) -> ContextSnapshot:
        return ContextSnapshot(
            measured=measured_tokens(messages),
            projected=projected_tokens(messages),
            window=self._window,
            budget=self.budget(),
        )

    def report(self, messages: Sequence[ModelMessage], view: ViewResult | None = None) -> ContextReport:
        return ContextReport(
            snapshot=self.snapshot(messages),
            instructions=self._instructions,
            tools=self._tools,
            history=estimate_messages_tokens(messages),
            cost=session_cost(messages),
            turns=turn_usage(messages),
            heaviest_tool_results=heaviest_tool_results(messages),
            view=view,
        )
