"""Estrategia que manda el historial tal cual."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens


@dataclass(frozen=True, slots=True)
class PassthroughStrategy:
    """Manda el historial tal cual. Es el comportamiento previo a este módulo."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        view = list(messages)
        return ViewResult(messages=view, estimated_tokens=estimate_messages_tokens(view))
