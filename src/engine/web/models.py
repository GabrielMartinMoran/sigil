"""Modelos de resultado de las tools de web."""

from __future__ import annotations

from pydantic import BaseModel

MAX_PAGE_CHARS = 40_000
"""Caracteres que se devuelven por página."""

MAX_SEARCH_RESULTS = 20
"""Tope de resultados por búsqueda."""


class SearchResult(BaseModel):
    """Un resultado de búsqueda web."""

    title: str
    url: str
    snippet: str


class SearchResponse(BaseModel):
    """Resultado de una búsqueda web."""

    query: str
    count: int
    results: list[SearchResult]


class PageContent(BaseModel):
    """Contenido extraído de una URL."""

    url: str
    format: str
    content: str
    truncated: bool = False
