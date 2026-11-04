"""Contenido extraído de una URL."""

from __future__ import annotations

from pydantic import BaseModel


class PageContent(BaseModel):
    """Contenido extraído de una URL."""

    url: str
    format: str
    content: str
    truncated: bool = False
