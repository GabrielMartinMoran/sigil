"""Result of patching a file."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatchResult(BaseModel):
    """Result of patching a file."""

    path: str
    mode: str = Field(description='`string` or `lines`.')
    replacements: int
    bytes_before: int
    bytes_after: int
    preview: str
