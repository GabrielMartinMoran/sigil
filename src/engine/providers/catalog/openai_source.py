"""OpenAI's catalogue, which answers in two different shapes.

One provider, two endpoints behind the same key, and they disagree about everything:

- **The public API** (`GET /v1/models`) answers ``{"data": [{"id": "gpt-5"}]}``: ids
  and nothing else — no window, no reasoning levels, no dialect. Capabilities come
  from pydantic-ai's bundled profiles, which is the kind of framework knowledge an
  adapter is allowed to have and the domain is not.
- **The ChatGPT plan** answers ``{"models": [{"slug": ..., "display_name": ...,
  "context_window": ..., "supported_reasoning_levels": [...]}]}``: everything a
  capability needs, but under different names.

Reading only the first shape is what made a signed-in plan show four hardcoded
fallback models with "The provider returned an empty model list" next to them: the
request succeeded, and the parser looked for ``data`` and found nothing.

The profile lookup is injected so the parser can be tested against a small table
instead of the real (and fast-moving) bundled data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.engine.providers.catalog.source import FetchJson, as_list
from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.model_capability import (
    ModelCapability,
    accepts_image_input,
    normalize_efforts,
)
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import CatalogError
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.thinking import ThinkingEffort

MODELS_URL = 'https://api.openai.com/v1/models'
DIALECT = 'responses'
"""Sigil talks to OpenAI through the Responses API: it carries reasoning natively."""

PLAN_KEY = 'models'
"""Key of the array in the plan payload; the public one uses ``data``."""

IDENTIFIER_KEYS = ('slug', 'id')
"""The id of a model, in the order the two shapes publish it."""

Profile = Mapping[str, Any]
ProfileLookup = Callable[[str], Profile | None]

_FAMILIES_TO_HIDE = ('audio', 'realtime', 'transcribe', 'tts', 'image', 'embedding', 'moderation', 'dall-e', 'whisper')
"""Families that cannot hold a chat turn. Listing them is noise, and picking one
fails at the first request instead of at the picker."""


class OpenAISource:
    """The model list of the OpenAI API, with a key or with a plan."""

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
            # `GET /v1/models` needs a credential even though the list is not secret.
            # For a plan account that credential is the OAuth access token, which is
            # why the caller sends whichever one the account has.
            return ()
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {secret}'}
        payload = await _fetch(fetch_json, account, headers)
        return parse_models(payload, self._profile_lookup)


async def _fetch(fetch_json: FetchJson, account: ProviderAccount, headers: dict[str, str]) -> Any:
    """The payload, with a rejection translated into something a person can act on.

    An HTTP error carries a status line and a link to MDN, which is not a sentence
    anyone can use. The two cases that actually happen have a cause worth naming:
    a plan token that expired, and a key the account was never given.
    """
    try:
        return await fetch_json(MODELS_URL, headers, 6.0)
    except Exception as exc:
        if _status_of(exc) in {401, 403}:
            raise CatalogError(_rejected_message(account)) from exc
        raise


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status of a transport error, when it has one."""
    response = getattr(exc, 'response', None)
    status = getattr(response, 'status_code', None)
    return status if isinstance(status, int) else None


def _rejected_message(account: ProviderAccount) -> str:
    """What to do about a credential the provider refused.

    A plan account reaches this point *after* its provider tried to renew the token:
    the request went through the SDK client, which refreshes from the `exp` hint and
    replays once on a 401. So a 401 here means the renewal itself failed, and the
    only thing left is a new sign-in — saying "send a message first" would send the
    user in a circle.
    """
    if account.auth is AuthMethod.OAUTH:
        return 'The ChatGPT plan session could not be renewed. Sign in again with /provider.'
    return 'OpenAI rejected the credential of this account. Check the API key with /provider.'


def parse_models(payload: Any, profile_lookup: ProfileLookup) -> tuple[ModelCapability, ...]:
    """The catalogue, from whichever shape this endpoint answered.

    The rich shape wins on every field it publishes; the bundled profile fills the
    rest. A model the profile knows and the payload does not — an old `data` entry —
    keeps working exactly as before.
    """
    models: list[ModelCapability] = []
    seen: set[str] = set()
    for entry in _entries(payload):
        name = _name(entry)
        if name is None or name in seen or not _is_chat_model(name):
            continue
        seen.add(name)
        models.append(_capability(name, entry, profile_lookup(name)))
    models.sort(key=lambda model: model.name)
    return tuple(models)


def _entries(payload: Any) -> list[dict[str, Any]]:
    """The array of models, in either shape.

    ``models`` (plan) is checked first because it is the more specific key: a payload
    carrying it is answering the richer endpoint, whatever else it happens to include.
    """
    if isinstance(payload, dict) and isinstance(payload.get(PLAN_KEY), list):
        return [entry for entry in payload[PLAN_KEY] if isinstance(entry, dict)]
    return as_list(payload)


def _name(entry: dict[str, Any]) -> str | None:
    """The id to send to the API: ``slug`` (plan) or ``id`` (public API)."""
    for key in IDENTIFIER_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _capability(name: str, entry: dict[str, Any], profile: Profile | None) -> ModelCapability:
    return ModelCapability(
        name=name,
        display_name=_display_name(entry, name),
        context_window=_window(entry, profile),
        efforts=_efforts(entry, profile),
        dialect=DIALECT,
        supports_images=_supports_images(entry, profile),
    )


def _display_name(entry: dict[str, Any], name: str) -> str:
    """``GPT-6.1-Sol`` when the plan publishes one, otherwise the id.

    The id stays in `name`: that is what the API takes, and the picker shows the
    readable one next to it.
    """
    value = entry.get('display_name')
    return value.strip() if isinstance(value, str) and value.strip() else name


def _window(entry: dict[str, Any], profile: Profile | None) -> int | None:
    """The usable window: ``context_window`` first, the profile when absent.

    ``max_context_window`` is deliberately not the first choice: on the plan it is the
    ceiling reachable with experimental context, several times the default, and using
    it would make the meter budget a turn that the provider is going to truncate.
    """
    declared = entry.get('context_window')
    if isinstance(declared, int) and declared > 0:
        return declared
    window = profile.get('context_window') if profile else None
    return window if isinstance(window, int) and window > 0 else None


def _efforts(entry: dict[str, Any], profile: Profile | None) -> tuple[ThinkingEffort, ...]:
    """The reasoning levels, from the payload when it lists them.

    The plan publishes ``supported_reasoning_levels`` as objects —
    ``[{"effort": "low", "description": ...}, ...]`` — and reading them as strings
    silently produced an empty list, which is a model that looks like it cannot
    reason. The bundled profile is the fallback for the id-only shape.
    """
    levels = entry.get('supported_reasoning_levels')
    if isinstance(levels, list) and levels:
        return normalize_efforts(_level_names(levels))
    if profile and profile.get('supports_thinking'):
        return normalize_efforts(profile.get('openai_reasoning_efforts') or ())
    return ()


def _level_names(levels: list[Any]) -> tuple[str, ...]:
    """The effort of each level entry, in whatever shape it came."""
    names: list[str] = []
    for level in levels:
        if isinstance(level, str):
            names.append(level)
            continue
        if isinstance(level, dict):
            effort = level.get('effort') or level.get('level')
            if isinstance(effort, str):
                names.append(effort)
    return tuple(names)


def _supports_images(entry: dict[str, Any], profile: Profile | None) -> bool:
    """Whether the model takes image input.

    Three sources, most specific first: the modalities the endpoint declares, then
    the bundled profile, and finally ``True`` — leaving the default is what lets the
    paste through for a model nobody has described yet.
    """
    modalities = entry.get('input_modalities')
    if isinstance(modalities, list):
        return accepts_image_input(modalities)
    if profile is not None and 'supports_image_input' in profile:
        return bool(profile['supports_image_input'])
    return True


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
