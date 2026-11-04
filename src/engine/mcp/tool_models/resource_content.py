"""Contenido de un recurso MCP."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResourceContent(BaseModel):
    """Contenido de un recurso MCP."""

    server: str
    uri: str
    mime_type: str | None = None
    binary: bool = False
    encoding: str = Field(default='utf-8', description='utf-8 for text, base64 for binary content.')
    content: str
