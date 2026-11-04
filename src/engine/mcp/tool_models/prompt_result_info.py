"""Resultado de resolver un prompt MCP."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.engine.mcp.tool_models.prompt_message_info import PromptMessageInfo


class PromptResultInfo(BaseModel):
    """Resultado de resolver un prompt MCP."""

    server: str
    name: str
    description: str | None = None
    messages: list[PromptMessageInfo] = Field(default_factory=list)
