"""OpenRouter's catalogue.

Public endpoint: no credential needed, which is why the account probe and the
catalogue can share it (the probe uses ``/api/v1/key`` instead).

Payload of ``GET /api/v1/models``, per entry:

- ``id`` and ``canonical_slug``: two names for the same route; both must resolve,
  because a user may paste either.
- ``context_length``: the window, sometimes absent for a brand new model.
- ``reasoning.supported_efforts``: ``["max", "high", ...]`` or missing when the
  model does not reason.

OpenRouter always proxies through its own Chat Completions endpoint, so the
dialect is fixed here and the per-model ``base_url`` is unused.
"""

from __future__ import annotations

from src.core.providers.catalog.model_catalog_source import FetchJson
from src.core.providers.catalog.openrouter_parse_models import parse_models
from src.core.providers.domain.model_capability import (
    ModelCapability,
)
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_kind import ProviderKind

MODELS_URL = 'https://openrouter.ai/api/v1/models'


class OpenRouterSource:
    """The model list of OpenRouter."""

    def supports(self, account: ProviderAccount) -> bool:
        return account.kind is ProviderKind.OPENROUTER

    async def models(
        self,
        account: ProviderAccount,
        *,
        secret: str | None,
        fetch_json: FetchJson,
    ) -> tuple[ModelCapability, ...]:
        headers = {'Accept': 'application/json'}
        if secret:
            headers['Authorization'] = f'Bearer {secret}'
        payload = await fetch_json(MODELS_URL, headers, 6.0)
        return parse_models(payload)
