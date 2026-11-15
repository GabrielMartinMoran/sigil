"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RenameBody(BaseModel):
    """The new label for an account. The id, and saved sessions, stay valid."""

    new_alias: str = Field(min_length=1)
