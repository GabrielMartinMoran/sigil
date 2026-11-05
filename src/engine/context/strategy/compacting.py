"""View that collapses old turns into a deterministic summary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic_ai.messages import CompactionPart, ModelMessage, ModelRequest, ModelResponse, ThinkingPart

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.constants import (
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_REASONING,
    DEFAULT_REASONING_MAX_PERCENT,
    DEFAULT_REASONING_STEPS,
    DEFAULT_TOOL_RESULT_HEAD_CHARS,
    PINNED_TOOLS,
    ReasoningPolicy,
    reasoning_token_cap,
    window_for,
)
from src.engine.context.strategy.rewriting import (
    accepts_replayed_reasoning,
    count_images,
    fit_to_budget,
    keep_fitting_steps,
    keep_recent_steps,
    truncate_old_tool_results,
)
from src.engine.context.strategy.turn_summary import summarize_turn
from src.engine.context.strategy.turns import has_pinned_result, split_turns
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens
from src.engine.context.working_set import DEFAULT_WORKING_SET_LIMIT, render_working_set, touched_files


@dataclass(frozen=True, slots=True)
class CompactingStrategy:
    """View that collapses old turns into a deterministic summary.

    Recent turns stay intact: the model usually needs the exact details of the
    last turn on the next one. The old ones are replaced by a
    `CompactionPart` with the intent and the tool trail, without payloads.

    Turns that carry the result of a pinned tool (a loaded skill) are not
    compacted: their content is instruction, not data.

    The summary closes with the **working set**: the files the agent touched,
    derived from the full log. Without that, compacting erases the trail of what it was
    editing and the agent re-reads from scratch what it already had at hand.

    The log is not touched: compaction is a projection, just like the window.
    """

    keep_recent_turns: int = 2
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
    tool_result_head_chars: int = DEFAULT_TOOL_RESULT_HEAD_CHARS
    reasoning: ReasoningPolicy = DEFAULT_REASONING
    """Same policy as `WindowedStrategy`: ``off``, ``recent`` or ``all``."""
    reasoning_steps: int = DEFAULT_REASONING_STEPS
    """Steps of the round in progress that keep their reasoning under ``recent``."""
    reasoning_max_percent: int = DEFAULT_REASONING_MAX_PERCENT
    """Ceiling for the reasoning kept, as a percentage of the model's window."""
    max_summary_chars: int = 400
    provider: str | None = None
    """The provider the request is going to: signed reasoning is only replayed to
    the provider that produced it."""
    pinned_tools: frozenset[str] = PINNED_TOOLS
    include_working_set: bool = True
    """Whether the summary closes with the touched files."""
    working_set_limit: int = DEFAULT_WORKING_SET_LIMIT
    """How many files fit in the working set."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        working = list(messages)
        dropped_thinking = 0
        steps = window_for(self.reasoning, self.reasoning_steps)
        if steps is None:
            working, dropped_thinking = self._drop_reasoning_that_cannot_be_replayed(working)
        else:
            working, _, dropped_thinking = keep_recent_steps(
                working,
                steps=steps,
                provider=self.provider,
                max_tokens=reasoning_token_cap(budget.window, budget.available_for_history, self.reasoning_max_percent),
            )

        turns = split_turns(working)
        recent_start = max(0, len(turns) - self.keep_recent_turns)
        old_turns = turns[:recent_start]
        recent_turns = turns[recent_start:]

        compacted = 0
        dropped_images = 0
        result: list[ModelMessage] = []
        pending: list[str] = []

        def flush() -> None:
            if pending:
                result.append(ModelRequest(parts=[CompactionPart(content='\n\n'.join(pending))]))
                pending.clear()

        for turn in old_turns:
            if has_pinned_result(turn, self.pinned_tools):
                # Loaded instructions: kept whole and in place.
                flush()
                result.extend(turn)
                continue
            text = summarize_turn(turn, max_chars=self.max_summary_chars)
            if text:
                pending.append(text)
                compacted += 1
                # The summary keeps the intent, not the pixels: an image of a
                # compacted turn is gone, and saying so is part of the report.
                dropped_images += count_images(turn)

        if compacted and self.include_working_set:
            # The working set comes from the full log, not from the old turns: what
            # the agent touched a step ago is also part of what it is
            # doing. It goes at the end of the summary so it sits as close as
            # possible to the turn in progress.
            working_set = render_working_set(touched_files(messages, limit=self.working_set_limit))
            if working_set:
                pending.append(working_set)
        flush()

        truncated = 0
        for index, turn in enumerate(recent_turns):
            # Recent turns are truncated only if they are old inside the window.
            if index < len(recent_turns) - 1:
                turn, count = truncate_old_tool_results(
                    [turn],
                    recent_start=1,
                    max_chars=self.max_tool_result_chars,
                    head_chars=self.tool_result_head_chars,
                    pinned_tools=self.pinned_tools,
                )
                truncated += count
            result.extend(turn)

        dropped = 0
        available = budget.available_for_history
        if available is not None:
            result, dropped = self._fit_dropping_reasoning_first(result, available)

        return ViewResult(
            messages=result,
            estimated_tokens=estimate_messages_tokens(result),
            dropped_messages=dropped,
            dropped_thinking=dropped_thinking,
            truncated_results=truncated,
            compacted_turns=compacted,
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
        """Reasoning goes before a turn does, and the summary is never discarded."""
        kept = keep_fitting_steps(messages, available, provider=self.provider)
        if kept is not None:
            return kept, 0
        return fit_keeping_summary(messages, available, pinned_tools=self.pinned_tools)


def fit_keeping_summary(
    messages: list[ModelMessage],
    available: int,
    *,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Like `fit_to_budget`, but it never discards the compaction summary."""
    head: list[ModelMessage] = []
    rest = list(messages)
    if rest and isinstance(rest[0], ModelRequest) and any(isinstance(part, CompactionPart) for part in rest[0].parts):
        head = [rest.pop(0)]
    return fit_to_budget(rest, available, pinned_tools=pinned_tools, keep_head=head)
