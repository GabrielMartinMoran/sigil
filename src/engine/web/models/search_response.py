"""Resultado de una búsqueda web."""

from __future__ import annotations

from pydantic import BaseModel

from src.engine.web.models.search_result import SearchResult


class SearchResponse(BaseModel):
    """Resultado de una búsqueda web."""

    query: str
    count: int
    results: list[SearchResult]
