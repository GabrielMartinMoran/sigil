"""Measuring the context of a session and building the budget."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import DEFAULT_OUTPUT_RESERVE, DEFAULT_SAFETY_MARGIN_PERCENT, ContextBudget
from src.engine.context.meter.cost import session_cost
from src.engine.context.meter.report import ContextReport
from src.engine.context.meter.snapshot import ContextSnapshot
from src.engine.context.meter.usage import heaviest_tool_results, turn_usage
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens, measured_tokens, projected_tokens


class ContextMeter:
    """Measures the context of a session and builds the budget."""

    def __init__(
        self,
        *,
        window: int | None = None,
        output_reserve: int = DEFAULT_OUTPUT_RESERVE,
        safety_margin_percent: int = DEFAULT_SAFETY_MARGIN_PERCENT,
    ) -> None:
        self._window = window if window and window > 0 else None
        self._output_reserve = output_reserve
        self._safety_margin_percent = safety_margin_percent
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

    def stamp(self, session: Any) -> None:
        """Writes what this meter measured onto ``session``, for a later load.

        The two blocks are measured from `ModelRequestParameters`, which only exists
        while a request is being made: nothing else can rebuild them from the
        transcript, so the session is where they survive the process.
        """
        session.tools_tokens = self._tools
        session.instructions_tokens = self._instructions

    def restore(self, session: Any) -> None:
        """Adopts what a loaded session measured, for a meter that just started.

        The window is not restored here: it belongs to the model, and the app
        publishes the model's own after binding it.
        """
        self._tools = int(getattr(session, 'tools_tokens', 0) or 0)
        self._instructions = int(getattr(session, 'instructions_tokens', 0) or 0)

    def budget(self) -> ContextBudget:
        return ContextBudget(
            window=self._window,
            tools=self._tools,
            instructions=self._instructions,
            output_reserve=self._output_reserve,
            safety_margin_percent=self._safety_margin_percent,
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
