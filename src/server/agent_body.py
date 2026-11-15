"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentBody(BaseModel):
    """A profile to activate for the next turn."""

    name: str = Field(min_length=1)
