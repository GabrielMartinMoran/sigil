"""Una pregunta interactiva con sus opciones."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.engine.interaction.models.question_option import QuestionOption


class QuestionItem(BaseModel):
    """Una pregunta con sus opciones."""

    question: str = Field(min_length=1)
    options: list[QuestionOption] = Field(min_length=2, max_length=3)
    allow_custom: bool = True
