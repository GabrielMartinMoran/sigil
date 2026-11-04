"""Result of a web search."""

from __future__ import annotations

from pydantic import BaseModel

from src.engine.web.models.search_result import SearchResult


class SearchResponse(BaseModel):
    """Result of a web search."""

    query: str
    count: int
    results: list[SearchResult]
