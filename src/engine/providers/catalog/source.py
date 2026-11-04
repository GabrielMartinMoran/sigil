"""How a catalogue source is asked for models.

The port is deliberately tiny and every adapter takes its HTTP call as a
parameter. That is what lets the tests exercise the real parsers against
recorded payloads with no network, and what lets the service fall back to a
static list when the provider is down.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import CatalogError

JSONValue = Any

FetchJson = Callable[[str, Mapping[str, str], float], Awaitable[JSONValue]]
"""``fetch(url, headers, timeout) -> parsed JSON``.

Raising is fine: the service turns any failure into the static fallback.
"""

DEFAULT_TIMEOUT = 6.0
"""Seconds. A catalogue is a convenience; it must never stall a turn."""


class ModelCatalogSource(Protocol):
    """Fetches the model list of one account."""

    def supports(self, account: ProviderAccount) -> bool:
        """Whether this source knows how to answer for ``account``."""
        ...

    async def models(
        self,
        account: ProviderAccount,
        *,
        secret: str | None,
        fetch_json: FetchJson,
    ) -> tuple[ModelCapability, ...]:
        """The catalogue, in the order the provider listed it.

        Args:
            account: Whose catalogue to read.
            secret: API key, when the endpoint requires one.
            fetch_json: Injected HTTP call, so tests never hit the network.

        Raises:
            CatalogError: When the payload is unusable. The service catches it
                and keeps the static list.
        """
        ...


def as_list(payload: JSONValue, *, key: str = 'data') -> list[dict[str, Any]]:
    """Extracts the array from a JSON payload, or raises a ``CatalogError``."""
    if not isinstance(payload, dict):
        raise CatalogError('The provider answered with something that is not a JSON object.')
    entries = payload.get(key)
    if not isinstance(entries, list):
        raise CatalogError(f"The provider answer has no '{key}' list.")
    return [entry for entry in entries if isinstance(entry, dict)]
