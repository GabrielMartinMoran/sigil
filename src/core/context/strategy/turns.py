"""Grouping of the history into self-contained turns."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart


def split_prompts(messages: Sequence[ModelMessage]) -> list[list[ModelMessage]]:
    """Groups the history into turns, delimited by the user prompt.

    A turn is self-contained: prompt -> responses -> tool calls -> tool returns.
    It is also what makes a turn the unit of accounting and inspection.
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
