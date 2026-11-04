"""Agrupación del historial en turnos autocontenidos."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart


def split_turns(messages: Sequence[ModelMessage]) -> list[list[ModelMessage]]:
    """Agrupa el historial en turnos, delimitados por el prompt del usuario.

    Un turno es autocontenido: prompt → respuestas → tool calls → tool returns.
    Por eso se puede descartar entero sin dejar un `ToolReturnPart` huérfano, que
    es lo que el provider rechaza con un 400.
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
    """Si el turno trae el resultado de una tool pineada (instrucciones)."""
    if not pinned:
        return False
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name in pinned
        for message in turn
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
