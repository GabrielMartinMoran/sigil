"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EffortBody(BaseModel):
    """The reasoning level to apply to the model in effect."""

    effort: str = Field(min_length=1)
