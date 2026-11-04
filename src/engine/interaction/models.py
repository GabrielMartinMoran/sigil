"""Modelos de las preguntas interactivas."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
