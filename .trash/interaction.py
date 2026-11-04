"""Preguntas interactivas: puente entre tools del engine y la UI."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field
from src.engine.tools.tool_registry import ToolLogger, ToolRegistrar


class QuestionOption(BaseModel):
    """Una opción de respuesta."""

    label: str = Field(min_length=1)
    description: str | None = None
    recommended: bool = False


class QuestionItem(BaseModel):
    """Una pregunta con sus opciones."""

    question: str = Field(min_length=1)
    options: list[QuestionOption] = Field(min_length=2, max_length=3)
    allow_custom: bool = True


class QuestionAnswer(BaseModel):
    """Respuesta del usuario a una pregunta."""

    question: str
    answer: str
    custom: bool = False


class QuestionResult(BaseModel):
    """Resultado de una tanda de preguntas."""

    answers: list[QuestionAnswer]
    cancelled: bool = False


QuestionAsker = Callable[[list[QuestionItem]], Awaitable[list[QuestionAnswer] | None]]


class QuestionHub:
    """Registra la función de la UI que sabe preguntar al usuario."""

    def __init__(self) -> None:
        self._asker: QuestionAsker | None = None
        self._lock = asyncio.Lock()

    def set_asker(self, asker: QuestionAsker | None) -> None:
        self._asker = asker

    @property
    def available(self) -> bool:
        return self._asker is not None

    async def ask(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        if self._asker is None:
            raise RuntimeError('Interactive questions are not available in this interface.')
        async with self._lock:
            return await self._asker(questions)


def register_interaction_tools(agent: Any, hub: QuestionHub, logger: ToolLogger | None = None) -> None:
    """Registra la tool de preguntas en un agente de pydantic-ai."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool
    async def ask_user(questions: list[QuestionItem]) -> QuestionResult:
        """Ask the user one or more multiple-choice questions in the UI.

        Use this when requirements are materially ambiguous or an action needs
        explicit authorization. Each question offers 2-3 options; the first (or
        the one marked `recommended`) is highlighted as recommended. The UI adds
        a final "Other" option so the user can type a custom answer. Questions
        are asked one at a time and all answers are returned together. If the
        user cancels, `cancelled` is true and `answers` is empty.

        Prefer this over guessing when a decision changes the outcome. Do not
        use it for routine choices you can make yourself.

        Args:
            questions: Questions to ask. Each needs a prompt and 2-3 options.
                Mark at most one option as `recommended`; if none is marked,
                the first option is used.
        """
        if not questions:
            raise ValueError('`questions` cannot be empty.')
        answers = await hub.ask(questions)
        if answers is None:
            return QuestionResult(answers=[], cancelled=True)
        return QuestionResult(answers=answers)


__all__ = [
    'QuestionAnswer',
    'QuestionAsker',
    'QuestionHub',
    'QuestionItem',
    'QuestionOption',
    'QuestionResult',
    'register_interaction_tools',
]
