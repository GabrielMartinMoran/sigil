"""Web tools: búsqueda y extracción de páginas con ddgs."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Literal

from ddgs import DDGS
from pydantic import BaseModel

from src.engine.tools.tool_registry import ToolLogger, ToolRegistrar

MAX_PAGE_CHARS = 40_000
MAX_SEARCH_RESULTS = 20


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


def _search(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
    timelimit: str | None,
) -> list[dict[str, Any]]:
    return DDGS().text(
        query,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=max_results,
    )


def _extract(url: str, fmt: str) -> dict[str, Any]:
    return DDGS().extract(url, fmt=fmt)


def _as_result(raw: dict[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(raw.get('title') or ''),
        url=str(raw.get('href') or raw.get('url') or ''),
        snippet=str(raw.get('body') or raw.get('snippet') or ''),
    )


def register_web_tools(agent: Any, logger: ToolLogger | None = None) -> None:
    """Registra las tools de web en un agente de pydantic-ai."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool
    async def web_search(
        query: str,
        max_results: int = 8,
        region: str = 'us-en',
        safesearch: Literal['on', 'moderate', 'off'] = 'moderate',
        timelimit: Literal['d', 'w', 'm', 'y'] | None = None,
    ) -> SearchResponse:
        """Search the web and return ranked text results.

        Use this for current information, library documentation, release notes,
        or any fact that is not in the workspace. Each result has a title, URL
        and snippet. Read a promising result with `read_url`.

        Args:
            query: Search terms. Use natural language or keywords.
            max_results: Number of results to return (1-20).
            region: DuckDuckGo region code, e.g. `us-en`, `ar-es`, `wt-wt`.
            safesearch: `on`, `moderate` or `off`.
            timelimit: Optional recency filter: `d` (day), `w` (week),
                `m` (month) or `y` (year).
        """
        capped = max(1, min(max_results, MAX_SEARCH_RESULTS))
        raw = await asyncio.to_thread(_search, query, capped, region, safesearch, timelimit)
        results = [_as_result(item) for item in raw]
        return SearchResponse(query=query, count=len(results), results=results)

    @tools.tool
    async def read_url(
        url: str,
        fmt: str = 'text_markdown',
        max_chars: int = MAX_PAGE_CHARS,
    ) -> PageContent:
        """Fetch a URL and return its extracted text.

        Use this after `web_search` to read a result in full. HTML is converted
        to Markdown by default. Very long pages are truncated; request a smaller
        `max_chars` or read the page another way if needed.

        Args:
            url: Absolute URL to fetch, e.g. from a `web_search` result.
            fmt: `text_markdown` (default), `text_plain`, `text_rich`,
                `text` (raw HTML) or `content` (raw bytes as base64).
            max_chars: Maximum characters to return (default 40,000).
        """
        raw = await asyncio.to_thread(_extract, url, fmt)
        content = raw.get('content') or ''
        resolved_url = str(raw.get('url') or url)
        if isinstance(content, bytes):
            return PageContent(
                url=resolved_url,
                format='content',
                content=base64.b64encode(content).decode('ascii'),
            )
        text = str(content)
        truncated = len(text) > max_chars
        return PageContent(url=resolved_url, format=fmt, content=text[:max_chars], truncated=truncated)


__all__ = ['PageContent', 'SearchResponse', 'SearchResult', 'register_web_tools']
