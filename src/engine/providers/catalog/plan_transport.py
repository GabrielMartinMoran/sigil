"""Reading a catalogue with a plan account: the same request, with a token that renews.

A ChatGPT-plan account authenticates a *request* with an OAuth access token that lives
about an hour. The catalogue is one more request, so listing models used to fail with
"the session expired" until the user happened to send a message — the thing they were
trying to do was the thing that renewed the credential, and it was unreachable.

pydantic-ai already owns that lifecycle, and owns it well: it loads the credential from
the source we give it, refreshes proactively from the JWT `exp` hint, refreshes and
replays once on a 401, and hands the rotated tokens back to the store. Its provider
exposes an SDK client whose HTTP auth runs that whole sequence, so the honest fix is to
send the catalogue request through that client instead of calling `httpx` ourselves.

Two consequences worth stating:

- **No private API.** `provider.client` and the SDK's `get` are public; the refresh
  logic is the library's, not a copy of it, so it cannot drift from the token rules.
- **The token is persisted.** The rotated tokens go through `StoredCredentialSource`,
  so the next turn finds the renewed credential and the refresh is not repeated.

The choice is made once, in `resolver_for`, and handed to the catalogue as a callable.
It belongs there rather than in the catalogue's `models()` signature because it is a
property of *how an account is read*, not of the call: every read of the same account
needs the same transport, and a parameter said otherwise.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from src.engine.providers.catalog.source import FetchJson
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.secret import OAuthSecret
from src.engine.providers.store.secret_store import SecretStore

PLAN_BASE_URL = 'https://api.openai.com/v1'
"""The plan provider's REST base, used when the instance does not publish one."""

ProviderFactory = Callable[[OAuthSecret, ProviderAccount], Any]
"""Builds the ChatGPT-plan provider for an account and its credential."""

TransportFor = Callable[[ProviderAccount], FetchJson | None]
"""The transport to read one account's catalogue with, or ``None`` for the default."""


class PlanTransportError(RuntimeError):
    """The plan's client could not be built. The caller falls back to the plain path."""


def path_for(url: str, base_url: str) -> str:
    """The path of ``url`` under ``base_url``, for a client that owns the base.

    A URL outside the base keeps its own path: the SDK replaces the base with an
    absolute URL, which is the one escape hatch that still authenticates.
    """
    absolute = url.strip()
    base = base_url.rstrip('/')
    if absolute.startswith(base):
        remainder = absolute[len(base) :]
        return remainder or '/'
    parts = urlsplit(absolute)
    return parts.path or '/'


def plan_fetch_json(
    build_client: Callable[[], Awaitable[Any]],
    base_url: str,
) -> FetchJson:
    """A ``fetch_json`` that sends the request through a plan provider's SDK client.

    Args:
        build_client: Returns the ``AsyncOpenAI`` of the account. Async because
            building the provider may load the credential.
        base_url: The provider's base, used to translate the URL into a path.

    The signature matches the port, so a source cannot tell the difference: it asks
    for JSON and gets JSON. What differs is that the headers it passed are ignored —
    the client injects its own bearer, which is the one that refreshes.
    """

    async def fetch(url: str, headers: Mapping[str, str], timeout: float) -> Any:
        del headers  # the client's auth owns the credential; a header here would not refresh
        client = await build_client()
        import httpx2  # the SDK's own fork: `cast_to` expects its Response, not httpx's

        # The timeout travels as a request option: the SDK's `get` takes no `timeout`
        # argument of its own, and a catalogue that hangs is worse than no catalogue.
        response = await client.get(
            path_for(url, base_url),
            cast_to=httpx2.Response,
            options={'timeout': timeout},
        )
        return response.json()

    return fetch


def resolver_for(store: SecretStore, factory: ProviderFactory | None) -> TransportFor | None:
    """The transport chooser for a build, or ``None`` when nothing renews.

    Only a plan account of a provider that supports sign-in needs it: an API key does
    not expire, and routing it through the SDK would take on the SDK's page parser for
    no gain. A build without a factory cannot refresh, so it says so by returning
    ``None`` instead of pretending.
    """
    if factory is None:
        return None

    def resolve(account: ProviderAccount) -> FetchJson | None:
        if account.kind is not ProviderKind.OPENAI:
            return None
        secret = store.get(account.id)
        if not isinstance(secret, OAuthSecret):
            return None
        # One instance per read: the provider binds its refresh lock to the first
        # event loop that awaits it, and reads happen on whichever loop is current.
        provider = factory(secret, account)

        async def build_client() -> Any:
            return provider.client

        return plan_fetch_json(build_client, getattr(provider, 'base_url', PLAN_BASE_URL))

    return resolve


__all__ = [
    'PLAN_BASE_URL',
    'PlanTransportError',
    'ProviderFactory',
    'TransportFor',
    'path_for',
    'plan_fetch_json',
    'resolver_for',
]
