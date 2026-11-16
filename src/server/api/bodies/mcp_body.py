"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class McpBody(BaseModel):
    """One MCP server to enable or disable for this session."""

    name: str = Field(min_length=1)
    enabled: bool
