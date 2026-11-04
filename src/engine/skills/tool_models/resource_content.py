"""Content of a skill resource."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResourceContent(BaseModel):
    """Content of a skill resource."""

    skill: str
    path: str
    size: int
    truncated: bool
    binary: bool
    encoding: str = Field(description='utf-8 for text, base64 for binary content.')
    content: str
