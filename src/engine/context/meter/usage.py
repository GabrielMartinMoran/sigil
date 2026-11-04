"""Desglose de costo por turno y por tool result."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

from src.engine.context.strategy.turns import split_turns
from src.engine.context.tokens import estimate_message_tokens, estimate_tokens, part_text


@dataclass(frozen=True, slots=True)
class TurnUsage:
    """Costo de un turno de la conversación."""

    index: int
    label: str
    tokens: int
    messages: int


@dataclass(frozen=True, slots=True)
class ToolResultUsage:
    """Costo de un tool result, para encontrar los que se comen la ventana."""

    tool_name: str
    tokens: int
    chars: int


def _turn_label(turn: Sequence[ModelMessage]) -> str:
    """Primer texto del usuario en el turno, recortado."""
    for message in turn:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            text = ' '.join(part_text(part).split())
            if text:
                return text if len(text) <= 48 else text[:47] + '…'
    return '(no prompt)'


def turn_usage(messages: Sequence[ModelMessage]) -> list[TurnUsage]:
    """Costo de cada turno, en orden."""
    usage = []
    for index, turn in enumerate(split_turns(messages)):
        usage.append(
            TurnUsage(
                index=index,
                label=_turn_label(turn),
                tokens=sum(estimate_message_tokens(message) for message in turn),
                messages=len(turn),
            )
        )
    return usage


def heaviest_tool_results(messages: Sequence[ModelMessage], limit: int = 5) -> list[ToolResultUsage]:
    """Los ``limit`` tool results más caros."""
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
