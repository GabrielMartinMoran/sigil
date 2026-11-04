"""Prompt expuesto por un servidor MCP."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.engine.mcp.tool_models.prompt_argument_info import PromptArgumentInfo


class PromptInfo(BaseModel):
    """Prompt expuesto por un servidor MCP."""

    server: str
    name: str
    description: str | None = None
    arguments: list[PromptArgumentInfo] = Field(default_factory=list)
