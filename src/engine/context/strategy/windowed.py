"""Windowed view: trims reasoning, old tool results and old turns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic_ai.messages import ModelMessage, ModelResponse, ThinkingPart

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.constants import (
    DEFAULT_KEEP_RECENT_TURNS,
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_REASONING,
    DEFAULT_REASONING_STEPS,
    DEFAULT_TOOL_RESULT_HEAD_CHARS,
    PINNED_TOOLS,
    ReasoningPolicy,
    window_for,
)
from src.engine.context.strategy.rewriting import (
    accepts_replayed_reasoning,
    drop_old_images,
    fit_to_budget,
    keep_fitting_steps,
    keep_recent_steps,
    truncate_old_tool_results,
)
from src.engine.context.strategy.turns import split_turns
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens


@dataclass(frozen=True, slots=True)
class WindowedStrategy:
    """Windowed view: trims reasoning, old tool results and old turns.

    Order of the levers, from cheapest to most destructive:

    1. Trim reasoning: it adds nothing to the next step and is the most
       voluminous. The reasoning of the round in progress is the exception: it is
       what stops the agent from re-deciding a decision it already made, so the
       last `keep_thinking_steps` steps of that round are kept.
    2. Truncate tool results of old turns: 80% of the weight is usually there.
    3. Discard whole turns, oldest to newest.
    """

    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
    tool_result_head_chars: int = DEFAULT_TOOL_RESULT_HEAD_CHARS
    reasoning: ReasoningPolicy = DEFAULT_REASONING
    """What travels back to the model of its own reasoning.

    ``recent`` keeps the last `reasoning_steps` steps of the round in progress: that
    is where the agent re-reads what it already decided. Past rounds are a record
    and are resent on every request.
    """
    reasoning_steps: int = DEFAULT_REASONING_STEPS
    """Steps of the round in progress that keep their reasoning under ``recent``."""
    provider: str | None = None
    """The provider the request is going to. Signed reasoning is only replayed to
    the provider that produced it; ``None`` trusts the adapter."""
    pinned_tools: frozenset[str] = PINNED_TOOLS
    """Tools whose result is instruction and is never truncated."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        working = list(messages)
        dropped_thinking = 0

        steps = window_for(self.reasoning, self.reasoning_steps)
        if steps is None:
            working, dropped_thinking = self._drop_reasoning_that_cannot_be_replayed(working)
        else:
            working, _, dropped_thinking = keep_recent_steps(working, steps=steps, provider=self.provider)

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
            working, dropped_for_budget = self._fit_dropping_reasoning_first(working, available)
            dropped_messages = dropped_for_budget

        return ViewResult(
            messages=working,
            estimated_tokens=estimate_messages_tokens(working),
            dropped_messages=dropped_messages,
            dropped_thinking=dropped_thinking,
            truncated_results=truncated_results,
            dropped_images=dropped_images,
        )

    def _drop_reasoning_that_cannot_be_replayed(self, messages: list[ModelMessage]) -> tuple[list[ModelMessage], int]:
        """When everything is kept, a block the receiver rejects is still useless."""
        if self.provider is None:
            return messages, 0
        kept: list[ModelMessage] = []
        dropped = 0
        for message in messages:
            if not isinstance(message, ModelResponse):
                kept.append(message)
                continue
            parts = [
                part
                for part in message.parts
                if not isinstance(part, ThinkingPart) or accepts_replayed_reasoning(part, self.provider)
            ]
            dropped += len(message.parts) - len(parts)
            kept.append(message if len(parts) == len(message.parts) else replace(message, parts=parts))
        return kept, dropped

    def _fit_dropping_reasoning_first(
        self, messages: list[ModelMessage], available: int
    ) -> tuple[list[ModelMessage], int]:
        """Reasoning is the cheapest thing to lose: it goes before a turn does.

        Keeping the reasoning of the round is a lever like the others: if what the
        policy kept does not fit, the window shrinks until it does, and only then
        are tool results truncated and turns discarded.
        """
        kept = keep_fitting_steps(messages, available, provider=self.provider)
        if kept is not None:
            return kept, 0
        return fit_to_budget(messages, available, pinned_tools=self.pinned_tools)
