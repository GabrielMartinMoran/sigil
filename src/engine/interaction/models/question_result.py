"""Result of a batch of interactive questions."""

from __future__ import annotations

from pydantic import BaseModel

from src.engine.interaction.models.question_answer import QuestionAnswer


class QuestionResult(BaseModel):
    """Result of a batch of questions."""

    answers: list[QuestionAnswer]
    cancelled: bool = False
