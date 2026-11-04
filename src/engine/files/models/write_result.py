"""Resultado de escribir un archivo."""

from __future__ import annotations

from pydantic import BaseModel


class WriteResult(BaseModel):
    """Resultado de escribir un archivo."""

    path: str
    bytes_written: int
    created: bool
    appended: bool
