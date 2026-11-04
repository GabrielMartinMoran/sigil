"""Resultado de una tanda de preguntas interactivas."""

from __future__ import annotations

from pydantic import BaseModel

from src.engine.interaction.models.question_answer import QuestionAnswer


class QuestionResult(BaseModel):
    """Resultado de una tanda de preguntas."""

    answers: list[QuestionAnswer]
    cancelled: bool = False
