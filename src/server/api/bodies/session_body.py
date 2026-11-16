"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionBody(BaseModel):
    """A conversation to open."""

    id: str = Field(min_length=1)
