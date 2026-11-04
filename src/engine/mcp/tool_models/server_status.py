"""Estado de un servidor MCP configurado."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServerStatus(BaseModel):
    """Estado de un servidor MCP configurado."""

    name: str
    transport: str = Field(description='stdio or http.')
    enabled: bool
    prefix: str = Field(description="Prefix applied to this server's tool names.")
    tools: list[str] = Field(default_factory=list, description='Tool names as the model sees them.')
    error: str | None = None
    source: str = Field(description='Config file that declared this server.')
