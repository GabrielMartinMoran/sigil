"""Resource exposed by an MCP server."""

from __future__ import annotations

from pydantic import BaseModel


class ResourceInfo(BaseModel):
    """Resource exposed by an MCP server."""

    server: str
    uri: str
    name: str | None = None
    mime_type: str | None = None
    description: str | None = None
