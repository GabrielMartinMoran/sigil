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

from typing import Any

from src.core.providers.catalog.model_catalog_source import FetchJson
from src.core.providers.catalog.openai_parse_models import Profile, ProfileLookup, parse_models
from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.catalog_error import CatalogError
from src.core.providers.domain.model_capability import (
    ModelCapability,
)
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_kind import ProviderKind

MODELS_URL = 'https://api.openai.com/v1/models'
"""Sigil talks to OpenAI through the Responses API: it carries reasoning natively."""

"""Key of the array in the plan payload; the public one uses ``data``."""

"""The id of a model, in the order the two shapes publish it."""


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
