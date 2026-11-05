"""Windowed view: trims reasoning, old tool results and old turns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.constants import (
    DEFAULT_KEEP_RECENT_TURNS,
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_TOOL_RESULT_HEAD_CHARS,
    PINNED_TOOLS,
)
from src.engine.context.strategy.rewriting import (
    drop_old_images,
    drop_thinking,
    fit_to_budget,
    truncate_old_tool_results,
)
from src.engine.context.strategy.turns import split_turns
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens


@dataclass(frozen=True, slots=True)
class WindowedStrategy:
    """Windowed view: trims reasoning, old tool results and old turns.

    Order of the levers, from cheapest to most destructive:

    1. Drop `ThinkingPart`: it adds nothing to the next step and is the most
       voluminous. Verified safe with Anthropic through OpenRouter.
    2. Truncate tool results of old turns: 80% of the weight is usually there.
    3. Discard whole turns, oldest to newest.
    """

    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
    tool_result_head_chars: int = DEFAULT_TOOL_RESULT_HEAD_CHARS
    drop_thinking: bool = True
    keep_thinking: bool = False
    """Per-provider escape hatch: if a route requires round-tripping the
    reasoning, you turn it on here and nothing gets dropped."""
    pinned_tools: frozenset[str] = PINNED_TOOLS
    """Tools whose result is instruction and is never truncated."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        working = list(messages)
        dropped_thinking = 0

        if self.drop_thinking and not self.keep_thinking:
            working, dropped_thinking = drop_thinking(working)

        turns = split_turns(working)
        recent_start = max(0, len(turns) - self.keep_recent_turns)

        working, dropped_images = drop_old_images(turns, recent_start=recent_start)
        turns = split_turns(working)

        working, truncated_results = truncate_old_tool_results(
            turns,
            recent_start=recent_start,
            max_chars=self.max_tool_result_chars,
            head_chars=self.tool_result_head_chars,
            pinned_tools=self.pinned_tools,
        )

        dropped_messages = 0
        available = budget.available_for_history
        if available is not None:
            working, dropped_messages = fit_to_budget(working, available, pinned_tools=self.pinned_tools)

        return ViewResult(
            messages=working,
            estimated_tokens=estimate_messages_tokens(working),
            dropped_messages=dropped_messages,
            dropped_thinking=dropped_thinking,
            truncated_results=truncated_results,
            dropped_images=dropped_images,
        )
