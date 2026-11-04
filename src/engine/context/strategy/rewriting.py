"""Palancas que reescriben el historial: razonamiento, tool results y turnos."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ThinkingPart, ToolReturnPart

from src.engine.context.strategy.turns import has_pinned_result, split_turns
from src.engine.context.tokens import estimate_messages_tokens, estimate_tokens, tool_return_text


def drop_thinking(messages: list[ModelMessage]) -> tuple[list[ModelMessage], int]:
    """Saca los `ThinkingPart` de las respuestas del asistente."""
    result: list[ModelMessage] = []
    dropped = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            result.append(message)
            continue
        kept = [part for part in message.parts if not isinstance(part, ThinkingPart)]
        dropped += len(message.parts) - len(kept)
        result.append(message if len(kept) == len(message.parts) else dataclasses.replace(message, parts=kept))
    return result, dropped


def _truncate_text(text: str, head_chars: int) -> str:
    omitted = len(text) - head_chars
    tokens = estimate_tokens(text)
    return f'{text[:head_chars]}\n\n[tool result truncated: {omitted:,} chars omitted, ~{tokens:,} tokens total]'


def truncate_old_tool_results(
    turns: list[list[ModelMessage]],
    *,
    recent_start: int,
    max_chars: int,
    head_chars: int,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Trunca los tool results de los turnos viejos, dejando los recientes intactos.

    Mide el texto que el modelo realmente ve (`model_response_str()`), no el
    `str()` del objeto: las tools de Sigil devuelven modelos pydantic, y comparar
    contra `isinstance(content, str)` dejaba el truncado sin efecto.
    """
    result: list[ModelMessage] = []
    truncated = 0
    for index, turn in enumerate(turns):
        if index >= recent_start:
            result.extend(turn)
            continue
        for message in turn:
            if not isinstance(message, ModelRequest):
                result.append(message)
                continue
            parts = []
            changed = False
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name not in pinned_tools:
                    text = tool_return_text(part)
                    if len(text) > max_chars:
                        parts.append(dataclasses.replace(part, content=_truncate_text(text, head_chars)))
                        truncated += 1
                        changed = True
                        continue
                parts.append(part)
            result.append(dataclasses.replace(message, parts=parts) if changed else message)
    return result, truncated


def fit_to_budget(
    messages: list[ModelMessage],
    available: int,
    *,
    pinned_tools: frozenset[str] = frozenset(),
    keep_head: Sequence[ModelMessage] = (),
) -> tuple[list[ModelMessage], int]:
    """Descarta turnos enteros hasta entrar en el presupuesto.

    Prefiere descartar turnos sin resultados pineados: si hay que elegir entre
    perder un `read_file` viejo y perder las instrucciones de un skill, se pierde
    el `read_file`.

    ``keep_head`` son los mensajes que nunca se descartan (el resumen de
    compactación).
    """
    head = list(keep_head)
    turns = split_turns(list(messages))
    dropped = 0

    def total() -> int:
        return estimate_messages_tokens([*head, *(message for turn in turns for message in turn)])

    if estimate_messages_tokens([*head, *messages]) <= available:
        return [*head, *messages], 0

    while len(turns) > 1 and total() > available:
        victim = next(
            (index for index, turn in enumerate(turns[:-1]) if not has_pinned_result(turn, pinned_tools)),
            None,
        )
        if victim is None:
            # Todo lo descartable trae instrucciones: se cae el más viejo igual.
            victim = 0
        dropped += len(turns[victim])
        turns.pop(victim)

    return [*head, *(message for turn in turns for message in turn)], dropped
