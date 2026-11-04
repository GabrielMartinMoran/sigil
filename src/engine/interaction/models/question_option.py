"""Una opción de respuesta de una pregunta interactiva."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuestionOption(BaseModel):
    """Una opción de respuesta."""

    label: str = Field(min_length=1)
    description: str | None = None
    recommended: bool = False
