"""User answer to an interactive question."""

from __future__ import annotations

from pydantic import BaseModel


class QuestionAnswer(BaseModel):
    """User answer to a question."""

    question: str
    answer: str
    custom: bool = False
