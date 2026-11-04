"""Already rendered message of an MCP prompt."""

from __future__ import annotations

from pydantic import BaseModel


class PromptMessageInfo(BaseModel):
    """Already rendered message of an MCP prompt."""

    role: str
    content: str
