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

from typing import Any

from src.engine.providers.catalog.source import FetchJson, as_list
from src.engine.providers.domain.model_capability import (
    ModelCapability,
    accepts_image_input,
    normalize_efforts,
)
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.thinking import ThinkingEffort

MODELS_URL = 'https://opencode.ai/zen/v1/models'

DIALECT_PREFERENCE: tuple[str, ...] = ('chat-completions', 'responses', 'messages', 'generate-content')
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


def parse_models(payload: Any) -> tuple[ModelCapability, ...]:
    models: list[ModelCapability] = []
    seen: set[str] = set()
    for entry in as_list(payload):
        model = parse_entry(entry)
        if model is None or model.name in seen:
            continue
        seen.add(model.name)
        models.append(model)
    return tuple(models)


def parse_entry(entry: dict[str, Any]) -> ModelCapability | None:
    name = entry.get('id')
    if not isinstance(name, str) or not name:
        return None

    dialect, base_url = _dialect(entry)
    context = _limit(entry)
    return ModelCapability(
        name=name,
        display_name=_display(entry) or name,
        context_window=context,
        efforts=_efforts(entry),
        dialect=dialect,
        base_url=base_url,
        supports_tools=_supports_tools(entry),
        supports_images=_supports_images(entry),
    )


def _dialect(entry: dict[str, Any]) -> tuple[str, str | None]:
    api = entry.get('api')
    if not isinstance(api, dict):
        return 'chat-completions', None
    for candidate in DIALECT_PREFERENCE:
        block = api.get(candidate)
        if isinstance(block, dict):
            url = block.get('base_url')
            return candidate, url if isinstance(url, str) and url else None
    return 'chat-completions', None


def _limit(entry: dict[str, Any]) -> int | None:
    limit = entry.get('limit')
    if isinstance(limit, dict):
        context = limit.get('context')
        if isinstance(context, int) and context > 0:
            return context
    return None


def _efforts(entry: dict[str, Any]) -> tuple[ThinkingEffort, ...]:
    capabilities = entry.get('capabilities')
    if not isinstance(capabilities, dict):
        return ()
    reasoning = capabilities.get('reasoning')
    if not isinstance(reasoning, dict) or not reasoning.get('supported'):
        return ()
    raw = reasoning.get('effort')
    return normalize_efforts(raw if isinstance(raw, list) else ())


def _supports_tools(entry: dict[str, Any]) -> bool:
    capabilities = entry.get('capabilities')
    if not isinstance(capabilities, dict):
        return True
    tools = capabilities.get('tools')
    if not isinstance(tools, dict):
        return True
    return bool(tools.get('supported', True))


def _supports_images(entry: dict[str, Any]) -> bool:
    """Whether the model takes image input, from the catalogue's own modalities.

    The tokens here are MIME types (``image/png``), which is why the decision is
    delegated instead of compared against a literal.
    """
    modalities = entry.get('modalities')
    if not isinstance(modalities, dict):
        return True
    return accepts_image_input(modalities.get('input'))


def _display(entry: dict[str, Any]) -> str:
    raw = entry.get('name')
    return raw.strip() if isinstance(raw, str) else ''
