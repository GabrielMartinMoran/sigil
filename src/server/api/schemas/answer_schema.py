"""Request body for one API route."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AnswerSchema(BaseModel):
    """An answer to the questions the agent asked."""

    id: str
    answers: list[dict[str, Any]] | None = None
    """``None`` cancels: the tool reports that the user declined."""
