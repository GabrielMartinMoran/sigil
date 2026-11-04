"""A web search result."""

from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    """A web search result."""

    title: str
    url: str
    snippet: str
