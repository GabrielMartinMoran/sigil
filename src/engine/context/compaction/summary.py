"""The structured checkpoint that replaces an older span in the model view.

This module plans checkpoints without calling a model:

- ``summary_boundary`` and ``summary_request`` are pure: they decide which messages a
  summary would cover, and they never split a tool exchange or a protected tail.
- ``summary_generator`` is the provider adapter that executes a planned request.

The transcript is data, never instructions: the prompt wraps it and says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import (
    ModelMessage,
    NativeToolCallPart,
    NativeToolReturnPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from src.engine.context.compaction.identity import boundary_index, part_key


@dataclass(frozen=True, slots=True)
class Summary:
    """A checkpoint and the part key of the first message it does not replace."""

    text: str
    boundary: str


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """What a summary would cover, for a caller that can reach a model."""

    messages: tuple[ModelMessage, ...]
    boundary: str
    previous: str | None = None


def summary_boundary(messages: Sequence[ModelMessage], *, reasoning_blocks: int = 5, tool_calls: int = 20) -> int:
    """Return the first retained message index, preserving whole tool exchanges.

    ``0`` means nothing can be summarized safely: there is no span older than the
    protected tail, or the transcript holds an exchange a cut would break.
    """
    if reasoning_blocks < 0 or tool_calls < 0:
        raise ValueError('retention counts must be non-negative')
    if not messages:
        return 0

    reasoning: list[int] = []
    calls: list[int] = []
    pending: dict[str, int] = {}
    exchanges: list[tuple[int, int]] = []
    for index, message in enumerate(messages):
        for part in message.parts:
            if isinstance(part, ThinkingPart):
                reasoning.append(index)
            elif isinstance(part, (ToolCallPart, NativeToolCallPart)):
                call_id = part.tool_call_id
                if not call_id or call_id in pending:
                    return 0
                pending[call_id] = index
                calls.append(index)
            elif isinstance(part, (ToolReturnPart, NativeToolReturnPart)):
                call_id = part.tool_call_id
                if not call_id or call_id not in pending:
                    return 0
                exchanges.append((pending.pop(call_id), index))

    native = [
        index
        for index, message in enumerate(messages)
        if any(isinstance(part, (NativeToolCallPart, NativeToolReturnPart)) for part in message.parts)
    ]
    boundary = min([len(messages) - 1, *native])
    if reasoning_blocks and reasoning:
        boundary = min(boundary, reasoning[max(0, len(reasoning) - reasoning_blocks)])
    if tool_calls and calls:
        boundary = min(boundary, calls[max(0, len(calls) - tool_calls)])
    if pending:
        boundary = min(boundary, min(pending.values()))
    while True:
        adjusted = min([boundary, *(start for start, end in exchanges if start < boundary <= end)])
        if adjusted == boundary:
            return boundary
        boundary = adjusted


def summary_request(
    messages: Sequence[ModelMessage],
    *,
    boundary: str | None,
    previous: str | None,
    reasoning_blocks: int = 5,
    tool_calls: int = 20,
) -> SummaryRequest | None:
    """The span a summary would cover, or ``None`` when it would not advance."""
    start = boundary_index(messages, boundary)
    stop = summary_boundary(messages, reasoning_blocks=reasoning_blocks, tool_calls=tool_calls)
    if stop <= start:
        return None
    first = messages[stop]
    if not first.parts:
        return None
    key = part_key(first, first.parts[0], 0)
    matches = sum(
        part_key(message, part, offset) == key for message in messages for offset, part in enumerate(message.parts)
    )
    if matches != 1:
        return None
    return SummaryRequest(
        messages=tuple(messages[start:stop]),
        boundary=key,
        previous=previous,
    )


__all__ = ['Summary', 'SummaryRequest', 'summary_boundary', 'summary_request']
