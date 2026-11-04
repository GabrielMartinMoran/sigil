"""Mensaje ya renderizado de un prompt MCP."""

from __future__ import annotations

from pydantic import BaseModel


class PromptMessageInfo(BaseModel):
    """Mensaje ya renderizado de un prompt MCP."""

    role: str
    content: str
