"""Respuesta del usuario a una pregunta interactiva."""

from __future__ import annotations

from pydantic import BaseModel


class QuestionAnswer(BaseModel):
    """Respuesta del usuario a una pregunta."""

    question: str
    answer: str
    custom: bool = False
