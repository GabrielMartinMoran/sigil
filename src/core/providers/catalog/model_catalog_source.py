"""The ModelCatalogSource value of the providers layer."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.provider_account import ProviderAccount

JSONValue = Any


FetchJson = Callable[[str, Mapping[str, str], float], Awaitable[JSONValue]]


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
