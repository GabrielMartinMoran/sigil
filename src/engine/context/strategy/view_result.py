"""Resultado de proyectar el log a la vista del modelo."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage


@dataclass(frozen=True, slots=True)
class ViewResult:
    """Resultado de proyectar el log a la vista del modelo."""

    messages: list[ModelMessage]
    estimated_tokens: int
    dropped_messages: int = 0
    dropped_thinking: int = 0
    truncated_results: int = 0
    compacted_turns: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.dropped_messages or self.dropped_thinking or self.truncated_results or self.compacted_turns)
