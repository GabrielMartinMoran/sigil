"""Deterministic summary of a turn, without calling the model."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def clip(text: str, limit: int) -> str:
    """Collapses whitespace and clips to ``limit`` characters."""
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def summarize_turn(turn: Sequence[ModelMessage], *, max_chars: int) -> str:
    """Deterministic summary of a turn: what the user asked and what was done.

    It does not call the model. It keeps the intent and the tool trail; it drops the
    payloads, which is exactly what you want to lose.
    """
    prompt = ''
    actions: list[str] = []
    answer = ''

    for message in turn:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and not prompt:
                    content = part.content if isinstance(part.content, str) else str(part.content)
                    prompt = clip(content, max_chars)
                elif isinstance(part, ToolReturnPart):
                    outcome = 'ok' if part.outcome == 'success' else part.outcome
                    actions.append(f'{part.tool_name} → {outcome}')
                elif isinstance(part, RetryPromptPart):
                    actions.append(f'{part.tool_name or "tool"} → retry')
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    actions.append(f'{part.tool_name}({clip(str(part.args), 80)})')
                elif isinstance(part, TextPart) and part.content:
                    answer = clip(part.content, max_chars)

    lines = []
    if prompt:
        lines.append(f'User: {prompt}')
    if actions:
        lines.append(f'Actions: {"; ".join(actions)}')
    if answer:
        lines.append(f'Assistant: {answer}')
    return '\n'.join(lines)
