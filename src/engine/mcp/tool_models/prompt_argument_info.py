"""Argument of an MCP prompt."""

from __future__ import annotations

from pydantic import BaseModel


class PromptArgumentInfo(BaseModel):
    """Argument of an MCP prompt."""

    name: str
    description: str | None = None
    required: bool = False
