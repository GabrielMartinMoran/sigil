"""The one place that performs HTTP for the catalogues.

Thin on purpose: no retries, no caching, no parsing. A catalogue that takes more
than a few seconds is simply reported as unavailable, because the alternative is
a menu that hangs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from src.engine.providers.catalog.source import DEFAULT_TIMEOUT


async def fetch_json(url: str, headers: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GETs ``url`` and returns the decoded JSON.

    Raises:
        httpx.HTTPError: On a transport failure or a non-2xx answer. The caller
            decides what that means; here it is just a failure.
    """
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=dict(headers))
        response.raise_for_status()
        return response.json()
