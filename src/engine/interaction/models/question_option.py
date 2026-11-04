"""An answer option of an interactive question."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuestionOption(BaseModel):
    """An answer option."""

    label: str = Field(min_length=1)
    description: str | None = None
    recommended: bool = False
