"""Prompt exposed by an MCP server."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.engine.mcp.tool_models.prompt_argument_info import PromptArgumentInfo


class PromptInfo(BaseModel):
    """Prompt exposed by an MCP server."""

    server: str
    name: str
    description: str | None = None
    arguments: list[PromptArgumentInfo] = Field(default_factory=list)
