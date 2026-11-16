"""The summary_request helper of the context layer."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
)

from src.core.context.compaction.identity import boundary_index, part_key
from src.core.context.compaction.summary_boundary import summary_boundary
from src.core.context.compaction.summary_request import SummaryRequest


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
