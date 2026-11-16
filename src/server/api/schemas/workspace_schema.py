"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceSchema(BaseModel):
    """A directory to move the server to."""

    root: str = Field(min_length=1)
