"""OpenCode's catalogue.

``GET https://opencode.ai/zen/v1/models`` is the only public list OpenCode
publishes, and it is the authoritative one even for Go accounts: it carries the
wire dialect per model, which is what makes one account span Chat Completions,
Responses and Messages.

Per entry:

- ``limit.context``: the window.
- ``capabilities.reasoning.effort``: the accepted levels, absent when the model
  does not reason.
- ``api``: a map of dialect → ``{base_url, sdk, ...}``. A model can be served by
  exactly one dialect today, but the map is what we read rather than a guess
  from the model name.

The entry is also the enrichment source for Go accounts, whose own catalogue
does not exist (``/inference/go/openai/v1/models`` answers 404).
"""

from __future__ import annotations

from src.core.providers.catalog.model_catalog_source import FetchJson
from src.core.providers.catalog.opencode_parse_models import parse_models
from src.core.providers.domain.model_capability import (
    ModelCapability,
)
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_kind import ProviderKind

MODELS_URL = 'https://opencode.ai/zen/v1/models'

"""Which dialect to use when a model offers several. The first one wins.

Chat Completions goes first because it is the most predictable wire format for a
generic agent; Responses is next because it carries reasoning natively; Messages
last because it needs the Anthropic SDK. ``generate-content`` is listed so an
unknown model is still visible in the menu, with a notice when picked.
"""

SUPPORTED_DIALECTS: frozenset[str] = frozenset({'chat-completions', 'responses', 'messages'})


class OpenCodeSource:
    """The model list of OpenCode Zen, used for both variants."""

    def supports(self, account: ProviderAccount) -> bool:
        return account.kind is ProviderKind.OPENCODE

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
