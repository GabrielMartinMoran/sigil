"""The summary_boundary helper of the context layer."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
    NativeToolCallPart,
    NativeToolReturnPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)


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
