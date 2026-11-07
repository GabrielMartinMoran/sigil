"""Reading a plan account's catalogue with a token that renews itself.

What this covers: listing models is a request, so it needs a credential, and a plan
account's credential expires in about an hour. Before this, the catalogue sent the
stored token with a bare `httpx` call, so the moment it lapsed the list went static
with "the session expired" — and nothing in the app was going to renew it, because
renewal only happened on a model request the user could not reach without a list.

The fix routes the request through the provider's own SDK client. These tests pin the
properties that matter: the request goes through the client that refreshes, an API-key
account keeps the plain path, and the choice lives in the resolver rather than in the
catalogue's call signature.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import pytest

from src.engine.providers.catalog.catalog_service import ModelCatalogService
from src.engine.providers.catalog.openai_source import OpenAISource
from src.engine.providers.catalog.plan_transport import path_for, plan_fetch_json, resolver_for
from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant
from src.engine.providers.domain.secret import ApiKeySecret, OAuthSecret

MODELS_URL = 'https://api.openai.com/v1/models'
BASE_URL = 'https://api.openai.com/v1'


def _account(auth: AuthMethod = AuthMethod.OAUTH, kind: ProviderKind = ProviderKind.OPENAI) -> ProviderAccount:
    return ProviderAccount.create('acct', kind, ProviderVariant.DEFAULT, auth=auth)


# --- the URL becomes a path -------------------------------------------------


def test_a_url_under_the_base_becomes_its_path() -> None:
    """The client owns the base: sending an absolute URL would bypass it."""
    assert path_for(MODELS_URL, BASE_URL) == '/models'
    assert path_for(MODELS_URL, 'https://api.openai.com/v1/') == '/models'


def test_a_url_outside_the_base_keeps_its_own_path() -> None:
    assert path_for('https://proxy.example/v1/models', BASE_URL) == '/v1/models'


def test_the_base_itself_is_the_root_path() -> None:
    assert path_for(BASE_URL, BASE_URL) == '/'


# --- the transport ---------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """The part of the SDK client the transport uses: `get(path, cast_to, options)`."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, *, cast_to: Any, options: dict[str, Any]) -> _FakeResponse:
        self.calls.append({'path': path, 'cast_to': cast_to, 'options': options})
        return _FakeResponse(self.payload)


def test_the_request_goes_through_the_client_that_refreshes() -> None:
    """This is the whole point: the client's auth is what renews the token."""
    client = _FakeClient({'models': [{'slug': 'gpt-6.1-sol'}]})

    async def build() -> Any:
        return client

    payload = asyncio.run(plan_fetch_json(build, BASE_URL)(MODELS_URL, {'Authorization': 'Bearer stale'}, 6.0))

    assert payload == {'models': [{'slug': 'gpt-6.1-sol'}]}
    assert client.calls[0]['path'] == '/models'
    assert client.calls[0]['cast_to'] is httpx2.Response


def test_the_timeout_travels_as_a_request_option() -> None:
    """The SDK's `get` takes no `timeout` argument; passing one raises. A catalogue
    that hangs is worse than no catalogue, so the timeout has to survive."""
    client = _FakeClient({})

    async def build() -> Any:
        return client

    asyncio.run(plan_fetch_json(build, BASE_URL)(MODELS_URL, {}, 4.5))

    assert client.calls[0]['options'] == {'timeout': 4.5}


def test_the_headers_passed_in_are_ignored() -> None:
    """A header built from the stored token would carry the expired one; the client
    injects its own, and that is the one that gets refreshed."""
    client = _FakeClient({})

    async def build() -> Any:
        return client

    asyncio.run(plan_fetch_json(build, BASE_URL)(MODELS_URL, {'Authorization': 'Bearer stale'}, 6.0))
    assert 'headers' not in client.calls[0]


# --- who gets the renewing transport ---------------------------------------


class _Secrets:
    """The minimum of a `SecretStore` the resolver needs."""

    def __init__(self, secret: Any) -> None:
        self.secret = secret

    def get(self, _id: str) -> Any:
        return self.secret


def _provider(base_url: str = BASE_URL) -> Any:
    return type('Provider', (), {'base_url': base_url, 'client': _FakeClient({})})()


def test_only_a_plan_openai_account_gets_the_refreshing_transport() -> None:
    """An API key does not expire, so routing it through the SDK buys nothing."""
    resolve = resolver_for(_Secrets(ApiKeySecret(key='sk-test')), lambda _s, _a: _provider())
    assert resolve is not None
    assert resolve(_account(AuthMethod.API_KEY)) is None


def test_a_plan_account_gets_the_refreshing_transport() -> None:
    resolve = resolver_for(_Secrets(OAuthSecret(access_token='at', refresh_token='rt')), lambda _s, _a: _provider())
    assert resolve is not None
    assert resolve(_account(AuthMethod.OAUTH)) is not None


def test_another_provider_keeps_the_plain_path() -> None:
    resolve = resolver_for(
        _Secrets(OAuthSecret(access_token='at', refresh_token='rt')),
        lambda _s, _a: _provider('https://opencode.ai/zen/v1'),
    )
    assert resolve is not None
    assert resolve(_account(AuthMethod.OAUTH, kind=ProviderKind.OPENCODE)) is None


def test_without_a_provider_factory_there_is_no_resolver() -> None:
    """A build that did not enable sign-in cannot refresh, and must not pretend to."""
    assert resolver_for(_Secrets(OAuthSecret(access_token='at', refresh_token='rt')), None) is None


def test_the_resolver_builds_a_provider_per_read() -> None:
    """The provider binds its refresh lock to the first loop that awaits it, so a
    shared instance would break the second loop that reads the catalogue."""
    built: list[int] = []

    def factory(_secret: Any, _account: Any) -> Any:
        built.append(1)
        return _provider()

    resolve = resolver_for(_Secrets(OAuthSecret(access_token='at', refresh_token='rt')), factory)
    assert resolve is not None
    resolve(_account())
    resolve(_account())

    assert len(built) == 2


# --- the catalogue uses it -------------------------------------------------


def _catalogue() -> ModelCatalogService:
    return ModelCatalogService(sources=(OpenAISource(profile_lookup=lambda _n: None),))


def test_the_catalogue_sends_a_plan_account_through_the_resolver() -> None:
    seen: list[str] = []

    async def injected(url: str, headers: Any, timeout: float) -> Any:
        seen.append(url)
        return {'models': [{'slug': 'gpt-6.1-sol', 'context_window': 500_000}]}

    service = ModelCatalogService(
        sources=(OpenAISource(profile_lookup=lambda _n: None),),
        transport_for=lambda _account: injected,
    )
    catalog = asyncio.run(service.models(_account(), secret='token'))

    assert seen == [MODELS_URL]
    assert catalog.source == 'remote'
    assert catalog.find('gpt-6.1-sol') is not None


def test_a_resolver_that_declines_falls_back_to_the_plain_transport() -> None:
    """The resolver answers for one kind of account; the rest keep the default."""
    used: list[str] = []

    async def default(url: str, headers: Any, timeout: float) -> Any:
        used.append('default')
        return {'models': [{'slug': 'gpt-6.1-sol'}]}

    service = ModelCatalogService(
        sources=(OpenAISource(profile_lookup=lambda _n: None),),
        fetch_json=default,
        transport_for=lambda _account: None,
    )
    asyncio.run(service.models(_account(), secret='token'))

    assert used == ['default']


def test_a_second_read_is_served_from_the_cache() -> None:
    """Loading the list on every turn would spend a refresh for nothing."""
    calls: list[int] = []

    async def injected(url: str, headers: Any, timeout: float) -> Any:
        calls.append(1)
        return {'models': [{'slug': 'gpt-6.1-sol'}]}

    service = ModelCatalogService(
        sources=(OpenAISource(profile_lookup=lambda _n: None),),
        transport_for=lambda _account: injected,
    )
    account = _account()
    asyncio.run(service.models(account, secret='token'))
    asyncio.run(service.models(account, secret='token'))

    assert len(calls) == 1


# --- failures -----------------------------------------------------------------


def test_a_failed_renewal_names_the_sign_in() -> None:
    """After the refresh was attempted, a 401 means the renewal itself failed."""

    class _Rejected(Exception):
        response = type('R', (), {'status_code': 401})()

    async def injected(url: str, headers: Any, timeout: float) -> Any:
        raise _Rejected()

    service = ModelCatalogService(
        sources=(OpenAISource(profile_lookup=lambda _n: None),), transport_for=lambda _a: injected
    )
    catalog = asyncio.run(service.models(_account(), secret='token'))

    assert catalog.source == 'static'
    assert catalog.error is not None
    assert 'could not be renewed' in catalog.error
    assert 'Sign in again with /provider' in catalog.error


@pytest.mark.parametrize('status', [401, 403])
def test_a_rejected_plan_credential_is_never_a_traceback(status: int) -> None:
    class _Rejected(Exception):
        response = type('R', (), {'status_code': status})()

    async def injected(url: str, headers: Any, timeout: float) -> Any:
        raise _Rejected()

    service = ModelCatalogService(
        sources=(OpenAISource(profile_lookup=lambda _n: None),), transport_for=lambda _a: injected
    )
    catalog = asyncio.run(service.models(_account(), secret='token'))

    assert catalog.error is not None
    assert 'Traceback' not in catalog.error
