"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ForkBody(BaseModel):
    """A fork point: the instant before this turn becomes a new session."""

    turn_id: str = Field(min_length=1)
