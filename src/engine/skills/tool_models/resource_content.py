"""Contenido de un recurso de un skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResourceContent(BaseModel):
    """Contenido de un recurso de un skill."""

    skill: str
    path: str
    size: int
    truncated: bool
    binary: bool
    encoding: str = Field(description='utf-8 for text, base64 for binary content.')
    content: str
