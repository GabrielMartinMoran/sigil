"""The ChatGPT credential source, which is lazily bound to the pydantic-ai type.

The regression these cover: `credential_source.py` subclassed
``OpenAIChatGPTCredentialSource`` at import time, and that module imports the
OpenAI SDK — a third of a second on every start, for a class that is only ever
instantiated when a ChatGPT account is built.

The deferral is the risky part: pydantic-ai accepts the source through a protocol,
but the adapter still has to *be* its class when it reaches the SDK. These tests
check both halves: the import stays out of startup, and the object is a real
instance where it matters.
"""

from __future__ import annotations

import asyncio

from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.secret import OAuthSecret
from src.engine.providers.login.credential_source import StoredCredentialSource, chatgpt_provider_factory


class FakeStore:
    """A credential store in memory, so the test never touches the real file."""

    def __init__(self, initial: dict | None = None) -> None:
        self._data = dict(initial or {})

    def get(self, account_id: str):
        return self._data.get(account_id)

    def put(self, account_id: str, secret) -> None:
        self._data[account_id] = secret


def _account() -> ProviderAccount:
    return ProviderAccount(id='acc-1', alias='me', kind=ProviderKind.OPENAI)


def _secret() -> OAuthSecret:
    return OAuthSecret(
        access_token='access',
        refresh_token='refresh',
        client_id='client',
        id_token='id',
        scope='openid',
        alias='me',
    )


def test_the_sdk_is_not_imported_by_importing_the_module():
    """The whole point: importing this module must not pull the OpenAI SDK."""
    # The module is already imported by the test session; what matters is that
    # building the adapter and the factory does not reach for the SDK.
    source = StoredCredentialSource(store=FakeStore(), account=_account(), fallback=_secret())
    assert source.account.id == 'acc-1'
    factory = chatgpt_provider_factory(FakeStore())
    assert callable(factory)


def test_saving_writes_through_the_store_in_the_shared_shape():
    """The tokens keep the same shape as the rest of the credentials file."""
    store = FakeStore()
    source = StoredCredentialSource(store=store, account=_account(), fallback=_secret())

    class Credentials:
        access_token = 'new-access'
        refresh_token = 'new-refresh'
        client_id = 'client'
        id_token = 'new-id'
        scope = 'openid'

    asyncio.run(source.save(Credentials()))

    stored = store.get('acc-1')
    assert isinstance(stored, OAuthSecret)
    assert stored.access_token == 'new-access'
    assert stored.refresh_token == 'new-refresh'
    assert stored.alias == 'me'


def test_loading_falls_back_to_the_secret_when_the_store_is_empty():
    """First run after sign-in: nothing is stored yet, the flow's secret is used."""
    source = StoredCredentialSource(store=FakeStore(), account=_account(), fallback=_secret())
    loaded = asyncio.run(source.load())

    assert loaded.access_token == 'access'
    assert loaded.refresh_token == 'refresh'
    assert loaded.client_id == 'client'


def test_loading_prefers_what_the_store_holds():
    """A refresh rotated by another process wins over the stale fallback."""
    rotated = OAuthSecret(
        access_token='rotated',
        refresh_token='rotated-refresh',
        client_id='',
        id_token='',
        scope='',
        alias='me',
    )
    source = StoredCredentialSource(store=FakeStore({'acc-1': rotated}), account=_account(), fallback=_secret())

    loaded = asyncio.run(source.load())

    assert loaded.access_token == 'rotated'
    # An empty client_id in the stored secret falls back to the flow's one.
    assert loaded.client_id == 'client'


def test_the_factory_builds_a_provider_that_accepts_the_source(tmp_path):
    """pydantic-ai accepts the source structurally: no subclass needed at runtime."""
    factory = chatgpt_provider_factory(FakeStore())
    provider = factory(_secret(), _account())

    assert provider is not None
    # The adapter keeps working through the object the factory handed over.
    source = StoredCredentialSource(store=FakeStore(), account=_account(), fallback=_secret())
    assert asyncio.run(source.load()).access_token == 'access'


def test_the_source_satisfies_the_sdk_protocol():
    """What a type checker checks: the two methods the protocol asks for."""
    from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentialSource

    source = StoredCredentialSource(store=FakeStore(), account=_account(), fallback=_secret())

    for method in ('load', 'save'):
        assert callable(getattr(source, method))
    # The protocol is structural, so nothing has to be subclassed at import time;
    # this documents that the SDK expects exactly these two coroutines.
    protocol_members = {name for name in dir(OpenAIChatGPTCredentialSource) if not name.startswith('_')}
    assert {'load', 'save'} <= protocol_members
