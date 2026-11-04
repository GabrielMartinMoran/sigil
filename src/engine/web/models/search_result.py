"""Un resultado de búsqueda web."""

from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    """Un resultado de búsqueda web."""

    title: str
    url: str
    snippet: str
