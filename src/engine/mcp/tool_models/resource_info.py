"""Recurso expuesto por un servidor MCP."""

from __future__ import annotations

from pydantic import BaseModel


class ResourceInfo(BaseModel):
    """Recurso expuesto por un servidor MCP."""

    server: str
    uri: str
    name: str | None = None
    mime_type: str | None = None
    description: str | None = None
