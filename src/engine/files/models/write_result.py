"""Result of writing a file."""

from __future__ import annotations

from pydantic import BaseModel


class WriteResult(BaseModel):
    """Result of writing a file."""

    path: str
    bytes_written: int
    created: bool
    appended: bool
