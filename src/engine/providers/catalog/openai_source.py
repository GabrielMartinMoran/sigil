"""OpenAI's catalogue.

``GET /v1/models`` returns ids and nothing else: no window, no reasoning levels,
no dialect. The capabilities therefore come from pydantic-ai's bundled profiles,
which is exactly the kind of framework knowledge an adapter is allowed to have
and the domain is not.

The profile lookup is injected so the parser can be tested against a small
table instead of the real (and fast-moving) bundled data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.engine.providers.catalog.source import FetchJson, as_list
from src.engine.providers.domain.model_capability import ModelCapability, normalize_efforts
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.thinking import ThinkingEffort

MODELS_URL = 'https://api.openai.com/v1/models'
DIALECT = 'responses'
"""Sigil talks to OpenAI through the Responses API: it carries reasoning natively."""

Profile = Mapping[str, Any]
ProfileLookup = Callable[[str], Profile | None]

_FAMILIES_TO_HIDE = ('audio', 'realtime', 'transcribe', 'tts', 'image', 'embedding', 'moderation', 'dall-e', 'whisper')
"""Families that cannot hold a chat turn. Listing them is noise, and picking one
fails at the first request instead of at the picker."""


class OpenAISource:
    """The model list of the OpenAI API."""

    def __init__(self, profile_lookup: ProfileLookup | None = None) -> None:
        self._profile_lookup = profile_lookup or _bundled_profile

    def supports(self, account: ProviderAccount) -> bool:
        return account.kind is ProviderKind.OPENAI

    async def models(
        self,
        account: ProviderAccount,
        *,
        secret: str | None,
        fetch_json: FetchJson,
    ) -> tuple[ModelCapability, ...]:
        if not secret:
            # `GET /v1/models` needs the key even though the list itself is not secret.
            return ()
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {secret}'}
        payload = await fetch_json(MODELS_URL, headers, 6.0)
        return parse_models(payload, self._profile_lookup)


def parse_models(payload: Any, profile_lookup: ProfileLookup) -> tuple[ModelCapability, ...]:
    models: list[ModelCapability] = []
    seen: set[str] = set()
    for entry in as_list(payload):
        name = entry.get('id')
        if not isinstance(name, str) or not name or name in seen:
            continue
        if not _is_chat_model(name):
            continue
        seen.add(name)
        models.append(_capability(name, profile_lookup(name)))
    models.sort(key=lambda model: model.name)
    return tuple(models)


def _capability(name: str, profile: Profile | None) -> ModelCapability:
    window = profile.get('context_window') if profile else None
    efforts: tuple[ThinkingEffort, ...] = ()
    if profile and profile.get('supports_thinking'):
        efforts = normalize_efforts(profile.get('openai_reasoning_efforts') or ())
    return ModelCapability(
        name=name,
        display_name=name,
        context_window=window if isinstance(window, int) and window > 0 else None,
        efforts=efforts,
        dialect=DIALECT,
    )


def _is_chat_model(name: str) -> bool:
    lowered = name.lower()
    return not any(fragment in lowered for fragment in _FAMILIES_TO_HIDE)


def _bundled_profile(name: str) -> Profile | None:
    """pydantic-ai's profile for ``name``, or ``None`` when it has none."""
    try:
        from pydantic_ai.profiles.openai import openai_model_profile
    except ImportError:  # pragma: no cover - pydantic-ai is a hard dependency
        return None
    try:
        return openai_model_profile(name)
    except Exception:  # noqa: BLE001 - an unknown model must not break the list
        return None
