"""How a catalogue source is asked for models.

The port is deliberately tiny and every adapter takes its HTTP call as a
parameter. That is what lets the tests exercise the real parsers against
recorded payloads with no network, and what lets the service fall back to a
static list when the provider is down.
"""

from __future__ import annotations

from typing import Any

from src.core.providers.catalog.model_catalog_source import JSONValue
from src.core.providers.domain.catalog_error import CatalogError

"""``fetch(url, headers, timeout) -> parsed JSON``.

Raising is fine: the service turns any failure into the static fallback.
"""

DEFAULT_TIMEOUT = 6.0
"""Seconds. A catalogue is a convenience; it must never stall a turn."""


def as_list(payload: JSONValue, *, key: str = 'data') -> list[dict[str, Any]]:
    """Extracts the array from a JSON payload, or raises a ``CatalogError``."""
    if not isinstance(payload, dict):
        raise CatalogError('The provider answered with something that is not a JSON object.')
    entries = payload.get(key)
    if not isinstance(entries, list):
        raise CatalogError(f"The provider answer has no '{key}' list.")
    return [entry for entry in entries if isinstance(entry, dict)]
