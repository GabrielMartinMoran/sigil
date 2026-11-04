"""Argumento de un prompt MCP."""

from __future__ import annotations

from pydantic import BaseModel


class PromptArgumentInfo(BaseModel):
    """Argumento de un prompt MCP."""

    name: str
    description: str | None = None
    required: bool = False
