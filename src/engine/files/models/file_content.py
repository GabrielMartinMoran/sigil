"""Contenido leído de un archivo."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileContent(BaseModel):
    """Contenido leído de un archivo."""

    path: str
    size: int
    truncated: bool
    binary: bool
    encoding: str = Field(description='utf-8 for text, base64 for binary content.')
    content: str
    start_line: int | None = None
    end_line: int | None = None
    total_lines: int | None = None
