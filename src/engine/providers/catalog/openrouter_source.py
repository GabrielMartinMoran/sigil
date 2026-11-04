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

from collections.abc import Sequence
from typing import Any

from src.engine.providers.catalog.source import FetchJson, as_list
from src.engine.providers.domain.model_capability import ModelCapability, normalize_efforts
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind

MODELS_URL = 'https://openrouter.ai/api/v1/models'
DIALECT = 'chat-completions'


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


def parse_models(payload: Any) -> tuple[ModelCapability, ...]:
    """Turns the payload into capabilities. Duplicate routes collapse on ``id``."""
    seen: set[str] = set()
    models: list[ModelCapability] = []
    for entry in as_list(payload):
        model = _parse_entry(entry)
        if model is None or model.name in seen:
            continue
        seen.add(model.name)
        models.append(model)
    return tuple(models)


def _parse_entry(entry: dict[str, Any]) -> ModelCapability | None:
    name = entry.get('id')
    if not isinstance(name, str) or not name:
        return None
    context = entry.get('context_length')
    reasoning = entry.get('reasoning')
    efforts: Sequence[object] = ()
    if isinstance(reasoning, dict):
        raw = reasoning.get('supported_efforts')
        if isinstance(raw, list):
            efforts = raw
    return ModelCapability(
        name=name,
        display_name=_clean_name(entry.get('name')) or name,
        context_window=context if isinstance(context, int) and context > 0 else None,
        efforts=normalize_efforts(efforts),
        dialect=DIALECT,
        supports_tools=_supports_tools(entry),
    )


def _clean_name(raw: object) -> str:
    """``"Anthropic: Claude Sonnet 4.6"`` → ``"Claude Sonnet 4.6"``.

    The provider prefix is redundant in a list that is already scoped to one
    account, and it is what makes the rows too long to read.
    """
    if not isinstance(raw, str):
        return ''
    _, separator, rest = raw.partition(': ')
    return rest.strip() if separator else raw.strip()


def _supports_tools(entry: dict[str, Any]) -> bool:
    params = entry.get('supported_parameters')
    if not isinstance(params, list):
        # Unknown is treated as capable: hiding a model is worse than a 400 the
        # user can read.
        return True
    return 'tools' in params
