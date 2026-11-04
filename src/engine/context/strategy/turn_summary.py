"""Resumen determinístico de un turno, sin llamar al modelo."""

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
    """Colapsa espacios y recorta a ``limit`` caracteres."""
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def summarize_turn(turn: Sequence[ModelMessage], *, max_chars: int) -> str:
    """Resumen determinístico de un turno: qué pidió el usuario y qué se hizo.

    No llama al modelo. Conserva la intención y el rastro de tools; pierde los
    payloads, que es exactamente lo que se quiere perder.
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
