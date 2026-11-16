"""Cost breakdown per turn and per tool result."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

from src.core.context.meter.prompt_usage import PromptUsage
from src.core.context.meter.tool_result_usage import ToolResultUsage
from src.core.context.strategy.turns import split_prompts
from src.core.context.tokens import estimate_message_tokens, estimate_tokens, part_text


def _turn_label(turn: Sequence[ModelMessage]) -> str:
    """First user text in the turn, clipped."""
    for message in turn:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            text = ' '.join(part_text(part).split())
            if text:
                return text if len(text) <= 48 else text[:47] + '…'
    return '(no prompt)'


def turn_usage(messages: Sequence[ModelMessage]) -> list[PromptUsage]:
    """Cost of each turn, in order."""
    usage = []
    for index, turn in enumerate(split_prompts(messages)):
        usage.append(
            PromptUsage(
                index=index,
                label=_turn_label(turn),
                tokens=sum(estimate_message_tokens(message) for message in turn),
                messages=len(turn),
            )
        )
    return usage


def heaviest_tool_results(messages: Sequence[ModelMessage], limit: int = 5) -> list[ToolResultUsage]:
    """The ``limit`` most expensive tool results."""
    results: list[ToolResultUsage] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            text = part_text(part)
            results.append(
                ToolResultUsage(
                    tool_name=part.tool_name or '?',
                    tokens=estimate_tokens(text),
                    chars=len(text),
                )
            )
    results.sort(key=lambda item: item.tokens, reverse=True)
    return results[:limit]
