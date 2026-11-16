"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorktreeSchema(BaseModel):
    """A worktree to create and move into."""

    name: str = Field(min_length=1)
