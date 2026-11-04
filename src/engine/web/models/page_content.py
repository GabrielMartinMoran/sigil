"""Content extracted from a URL."""

from __future__ import annotations

from pydantic import BaseModel


class PageContent(BaseModel):
    """Content extracted from a URL."""

    url: str
    format: str
    content: str
    truncated: bool = False
