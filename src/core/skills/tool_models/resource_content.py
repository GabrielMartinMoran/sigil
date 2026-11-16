"""Content of a skill resource."""

from __future__ import annotations

from pydantic import BaseModel


class ResourceContent(BaseModel):
    """Content of a skill resource."""

    skill: str
    path: str
    size: int
    truncated: bool
    binary: bool
    note: str
