"""Resultado de parchear un archivo."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatchResult(BaseModel):
    """Resultado de parchear un archivo."""

    path: str
    mode: str = Field(description='`string` or `lines`.')
    replacements: int
    bytes_before: int
    bytes_after: int
    preview: str
