"""Grouping of the history into self-contained turns."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart


def split_prompts(messages: Sequence[ModelMessage]) -> list[list[ModelMessage]]:
    """Groups the history into turns, delimited by the user prompt.

    A turn is self-contained: prompt -> responses -> tool calls -> tool returns.
    That is why it can be discarded whole without leaving an orphan `ToolReturnPart`, which
    is what the provider rejects with a 400.
    """
    turns: list[list[ModelMessage]] = []
    current: list[ModelMessage] = []
    for message in messages:
        starts_turn = isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        )
        if starts_turn and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def has_pinned_result(turn: Sequence[ModelMessage], pinned: frozenset[str]) -> bool:
    """Whether the turn carries the result of a pinned tool (instructions)."""
    if not pinned:
        return False
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name in pinned
        for message in turn
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
