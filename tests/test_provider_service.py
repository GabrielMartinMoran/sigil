"""Tests for the provider service: the use cases the UI drives."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.engine.providers.bootstrap import ProviderPaths, build_service
from src.engine.providers.catalog.catalog_service import ModelCatalogService
from src.engine.providers.catalog.probe import ProbeResult
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.domain.provider_error import (
    AccountNotFound,
    AliasError,
    BusyError,
    CredentialError,
    LastAccountError,
    NoAccountError,
    ProviderError,
    SecretRejectedError,
)
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant
from src.engine.providers.domain.secret import ApiKeySecret, OAuthSecret
from src.engine.providers.domain.selection import Selection
from src.engine.providers.factory.unconfigured import UnconfiguredModel
from src.engine.providers.service import ModelService
from src.engine.providers.store.secret_store import FileSecretStore

CATALOG = ModelCatalog(
    models=(
        ModelCapability(name='first/model', context_window=100_000, efforts=('low', 'high')),
        ModelCapability(name='second/model', context_window=200_000, efforts=('low', 'medium', 'high', 'max')),
    ),
    source='remote',
)


def _service(tmp_path: Path, *, probe=None, busy: bool = False, catalog: ModelCatalog = CATALOG) -> ModelService:
    async def fetch(url, headers, timeout):
        return {'data': []}

    catalog_service = ModelCatalogService(fetch_json=fetch)
    service = build_service(
        ProviderPaths(tmp_path / 'providers'),
        session_id='sess-test',
        initial_model=UnconfiguredModel(),
        probe=probe or _accept,
        is_busy=lambda: busy,
        catalog=catalog_service,
        oauth_provider_factory=lambda credential, account: _fake_openai_provider(),
    )
    # Pre-seed the cache so `select` sees capabilities without network.
    _seed_catalog(service, catalog)
    return service


def _fake_openai_provider():
    from pydantic_ai.providers.openai import OpenAIProvider

    return OpenAIProvider(api_key='from-oauth')


async def _accept(_account, _key):
    return ProbeResult('valid')


async def _reject(_account, _key):
    return ProbeResult('rejected', 'invalid_api_key')


def _seed_catalog(service: ModelService, catalog: ModelCatalog) -> None:
    original = service.catalog.models

    async def models(account, *, secret=None):
        service.catalog._cache.put(account.id, catalog)
        return catalog

    service.catalog.models = models  # type: ignore[method-assign]
    del original


def _add(service: ModelService, alias='work', kind=ProviderKind.OPENROUTER, variant=ProviderVariant.DEFAULT):
    return asyncio.run(service.add_api_key(kind, alias, 'sk-test', variant=variant))


# --- adding -----------------------------------------------------------------


def test_add_api_key_stores_the_account_and_selects_a_model(tmp_path):
    service = _service(tmp_path)
    snapshot = _add(service)

    assert snapshot.is_configured
    assert snapshot.alias == 'work'
    assert snapshot.model == 'first/model'
    assert len(service.list_accounts()) == 1


def test_add_api_key_persists_the_secret_privately(tmp_path):
    service = _service(tmp_path)
    _add(service)
    path = tmp_path / 'providers' / 'credentials.json'
    assert oct(path.stat().st_mode)[-3:] == '600'
    assert 'sk-test' in path.read_text(encoding='utf-8')


def test_add_api_key_rejects_a_bad_alias_before_any_network_call(tmp_path):
    calls: list[str] = []

    async def probe(account, key):
        calls.append(key)
        return ProbeResult('valid')

    service = _service(tmp_path, probe=probe)
    with pytest.raises(AliasError):
        asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, '   ', 'sk-test'))
    assert calls == []


def test_add_api_key_rejects_a_duplicate_alias_ignoring_case(tmp_path):
    service = _service(tmp_path)
    _add(service, 'Work')
    with pytest.raises(AliasError):
        asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, 'work', 'sk-other'))


def test_add_api_key_does_not_persist_a_rejected_credential(tmp_path):
    service = _service(tmp_path, probe=_reject)
    with pytest.raises(SecretRejectedError):
        asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, 'work', 'sk-bad'))

    assert service.list_accounts() == ()
    assert not (tmp_path / 'providers' / 'accounts.json').exists()


def test_add_api_key_records_a_warning_when_the_probe_could_not_decide(tmp_path):
    async def unknown(account, key):
        return ProbeResult('unknown', 'network is down')

    service = _service(tmp_path, probe=unknown)
    snapshot = asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, 'work', 'sk-test'))

    assert snapshot.is_configured
    account = service.list_accounts()[0]
    assert service.last_probe(account.id).outcome == 'unknown'


def test_add_api_key_rejects_a_key_with_spaces(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(CredentialError):
        asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, 'work', 'sk test'))


def test_add_api_key_accepts_several_accounts_of_the_same_kind(tmp_path):
    service = _service(tmp_path)
    _add(service, 'personal')
    _add(service, 'work')

    assert [account.alias for account in service.list_accounts()] == ['work', 'personal']


def test_add_api_key_handles_the_opencode_variants(tmp_path):
    service = _service(tmp_path)
    snapshot = asyncio.run(service.add_api_key(ProviderKind.OPENCODE, 'go', 'sk-test', variant=ProviderVariant.GO))
    assert snapshot.account.variant is ProviderVariant.GO
    assert snapshot.account.provider_label == 'OpenCode Go'


# --- listing ----------------------------------------------------------------


def test_account_views_put_the_active_one_first(tmp_path):
    service = _service(tmp_path)
    _add(service, 'first')
    _add(service, 'second')

    views = service.account_views()
    assert views[0].active
    assert views[0].account.alias == 'second'
    assert views[0].marker == '●'
    assert views[1].marker == '○'


# --- selection --------------------------------------------------------------


def test_select_swaps_the_routers_target_and_keeps_the_router_itself(tmp_path):
    """The router is what the agent holds: it must not be replaced.

    Replacing it would leave the agent pointing at the old object, which is
    exactly the bug that made a provider switch need a restart.
    """
    service = _service(tmp_path)
    _add(service)
    router = service.current_model()
    before = router.current

    snapshot = service.select('work', 'second/model', 'high')

    assert snapshot.model == 'second/model'
    assert snapshot.thinking == 'high'
    assert service.current_model() is router
    assert router.current is not before
    assert router.current.model_name == 'second/model'


def test_the_agent_keeps_the_router_so_a_switch_reaches_the_next_request(tmp_path):
    """End of the original bug: the agent was built with the concrete model."""
    from pydantic_ai import Agent

    service = _service(tmp_path)
    _add(service)
    agent = Agent(service.current_model(), instructions='x')
    assert agent.model is service.router

    service.select('work', 'second/model')

    assert agent.model.context_window == 200_000


def test_select_records_the_model_on_the_account(tmp_path):
    service = _service(tmp_path)
    _add(service)
    service.select('work', 'second/model')

    assert service.list_accounts()[0].last_model == 'second/model'


def test_select_falls_back_to_default_when_the_model_rejects_the_level(tmp_path):
    service = _service(tmp_path)
    _add(service)
    service.select('work', 'first/model', 'high')
    snapshot = service.select('work', 'second/model', 'max')

    assert snapshot.thinking == 'max'

    snapshot = service.select('work', 'first/model', 'max')
    assert snapshot.thinking == 'default'


def test_select_rejects_an_unknown_reasoning_level(tmp_path):
    service = _service(tmp_path)
    _add(service)
    with pytest.raises(ProviderError):
        service.select('work', 'first/model', 'ludicrous')


def test_select_refuses_while_a_turn_is_running(tmp_path):
    busy = {'now': False}
    service = _service(tmp_path, busy=False)
    service.is_busy = lambda: busy['now']
    _add(service)
    busy['now'] = True
    with pytest.raises(BusyError):
        service.select('work', 'second/model')


def test_select_of_an_unknown_alias_raises(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(AccountNotFound):
        service.select('nope', 'm')


def test_select_allows_a_model_no_catalogue_described(tmp_path):
    service = _service(tmp_path)
    _add(service)
    snapshot = service.select('work', 'custom/model-from-the-future')
    assert snapshot.model == 'custom/model-from-the-future'


# --- effort -----------------------------------------------------------------


def test_set_thinking_updates_the_selection(tmp_path):
    service = _service(tmp_path)
    _add(service)
    service.select('work', 'second/model')

    assert service.set_thinking('max').thinking == 'max'


def test_set_thinking_rejects_a_level_the_model_does_not_accept(tmp_path):
    service = _service(tmp_path)
    _add(service)
    service.select('work', 'first/model')
    with pytest.raises(ProviderError) as error:
        service.set_thinking('max')
    assert 'low/high' in str(error.value)


def test_set_thinking_without_a_selection_is_a_domain_error(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(NoAccountError):
        service.set_thinking('high')


def test_set_thinking_accepts_off_and_default(tmp_path):
    service = _service(tmp_path)
    _add(service)
    assert service.set_thinking('off').thinking == 'off'
    assert service.set_thinking('default').thinking == 'default'


# --- removal ----------------------------------------------------------------


def test_remove_deletes_the_account_and_its_credential(tmp_path):
    service = _service(tmp_path)
    _add(service, 'one')
    _add(service, 'two')
    account = service.find('two')

    service.remove('two')

    assert [item.alias for item in service.list_accounts()] == ['one']
    assert service.secrets.get(account.id) is None


def test_remove_refuses_to_leave_no_accounts(tmp_path):
    service = _service(tmp_path)
    _add(service)
    with pytest.raises(LastAccountError):
        service.remove('work')


def test_removing_the_active_account_activates_the_first_remaining(tmp_path):
    service = _service(tmp_path)
    _add(service, 'one')
    _add(service, 'two')
    assert service.snapshot().alias == 'two'

    service.remove('two')

    assert service.snapshot().alias == 'one'
    assert service.snapshot().model == 'first/model'


def test_renaming_keeps_the_active_selection_working(tmp_path):
    service = _service(tmp_path)
    _add(service, 'before')
    service.rename('before', 'after')

    assert service.snapshot().alias == 'after'
    assert service.snapshot().model == 'first/model'


def test_rename_rejects_a_taken_alias(tmp_path):
    service = _service(tmp_path)
    _add(service, 'one')
    _add(service, 'two')
    with pytest.raises(AliasError):
        service.rename('two', 'ONE')


# --- resolution -------------------------------------------------------------


def test_resolve_prefers_the_session_selection(tmp_path):
    service = _service(tmp_path)
    _add(service, 'one')
    _add(service, 'two')
    account = service.find('one')

    resolved = service.resolve(Selection(account_id=account.id, model='second/model', thinking='low'))

    assert resolved.account_id == account.id
    assert resolved.model == 'second/model'


def test_resolve_falls_back_to_the_stored_selection(tmp_path):
    service = _service(tmp_path)
    _add(service)
    assert service.resolve() == service.state.last_selection()


def test_resolve_ignores_a_session_pointing_at_a_removed_account(tmp_path):
    service = _service(tmp_path)
    _add(service, 'one')
    _add(service, 'two')
    gone = service.find('one').id
    service.remove('one')

    resolved = service.resolve(Selection(account_id=gone, model='second/model'))
    assert resolved.account_id != gone


def test_resolve_falls_back_to_an_account_that_remembers_a_model(tmp_path):
    service = _service(tmp_path)
    _add(service)
    service.state.set_last_selection(None)

    resolved = service.resolve()
    assert resolved is not None
    assert resolved.model == 'first/model'


def test_resolve_returns_nothing_when_no_account_exists(tmp_path):
    service = _service(tmp_path)
    assert service.resolve() is None


def test_apply_points_the_router_at_a_selection(tmp_path):
    service = _service(tmp_path)
    _add(service, 'one')
    _add(service, 'two')
    target = service.find('one')

    snapshot = service.apply(Selection(account_id=target.id, model='second/model', thinking='high'))

    assert snapshot.alias == 'one'
    assert snapshot.model == 'second/model'
    assert snapshot.thinking == 'high'


# --- snapshot ---------------------------------------------------------------


def test_snapshot_is_empty_before_anything_is_configured(tmp_path):
    service = _service(tmp_path)
    snapshot = service.snapshot()
    assert not snapshot.is_configured
    assert snapshot.label == 'no model'
    assert not service.is_configured()


def test_snapshot_reports_the_window_and_efforts_of_the_active_model(tmp_path):
    service = _service(tmp_path)
    _add(service)
    snapshot = service.snapshot()
    assert snapshot.window == 100_000
    assert snapshot.efforts == ('low', 'high')
    assert snapshot.label == 'work:first/model'


# --- OAuth ------------------------------------------------------------------


class _Flow:
    def __init__(self, secret: OAuthSecret) -> None:
        self._secret = secret
        self.calls: list[object] = []

    async def sign_in(self, account):
        self.calls.append(account)
        return self._secret


def _oauth_secret(**overrides) -> OAuthSecret:
    values = {
        'access_token': 'at',
        'refresh_token': 'rt',
        'client_id': 'cid',
        'scope': 'openid chatgpt.tokens.use.direct',
    }
    values.update(overrides)
    return OAuthSecret(**values)


def test_add_oauth_stores_the_credential_and_selects_a_model(tmp_path):
    service = _service(tmp_path)
    flow = _Flow(_oauth_secret())

    snapshot = asyncio.run(service.add_oauth(ProviderKind.OPENAI, 'plan', flow))

    assert snapshot.is_configured
    assert snapshot.account.auth.value == 'oauth'
    stored = service.secrets.get(snapshot.account.id)
    assert isinstance(stored, OAuthSecret)
    assert stored.access_token == 'at'


def test_add_oauth_refuses_an_account_that_did_not_grant_plan_usage(tmp_path):
    service = _service(tmp_path)
    flow = _Flow(_oauth_secret(scope='openid profile'))

    with pytest.raises(ProviderError) as error:
        asyncio.run(service.add_oauth(ProviderKind.OPENAI, 'plan', flow))
    assert 'plan usage' in str(error.value)
    assert service.list_accounts() == ()


def test_add_oauth_is_only_offered_for_openai(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(ProviderError):
        asyncio.run(service.add_oauth(ProviderKind.OPENROUTER, 'nope', _Flow(_oauth_secret())))


def test_add_api_key_is_not_offered_for_oauth_only_paths(tmp_path):
    service = _service(tmp_path)
    # OpenAI accepts both; OpenRouter only keys. The guard is on the method.
    with pytest.raises(ProviderError):
        asyncio.run(service.add_oauth(ProviderKind.OPENCODE, 'nope', _Flow(_oauth_secret())))


# --- catalog ----------------------------------------------------------------


def test_catalog_for_uses_the_active_account(tmp_path):
    service = _service(tmp_path)
    _add(service)
    catalog = asyncio.run(service.catalog_for())
    assert catalog.find('first/model') is not None


def test_catalog_for_an_unknown_alias_raises(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(AccountNotFound):
        asyncio.run(service.catalog_for('nope'))


def test_catalog_for_without_an_account_raises(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(NoAccountError):
        asyncio.run(service.catalog_for())


# --- the credential a catalogue is read with --------------------------------
#
# A catalogue is a bearer-authenticated GET, and which bearer is right depends on how
# the account signed in. Returning nothing for a plan account made a signed-in
# ChatGPT subscription show four hardcoded models and an "empty list" error: the
# request was never made with a credential at all.


def test_a_plan_account_reads_its_catalogue_with_the_oauth_token(tmp_path):
    service = _service(tmp_path)
    flow = _Flow(_oauth_secret(access_token='the-plan-token'))
    asyncio.run(service.add_oauth(ProviderKind.OPENAI, 'plan', flow))
    account = service.accounts.load()[0]

    assert service._api_key_for(account) == 'the-plan-token'


def test_an_api_key_account_reads_its_catalogue_with_the_key(tmp_path):
    service = _service(tmp_path)
    _add(service)
    account = service.accounts.load()[0]

    assert service._api_key_for(account) == 'sk-test'


def test_an_account_without_a_secret_has_nothing_to_send(tmp_path):
    service = _service(tmp_path)
    flow = _Flow(_oauth_secret())
    asyncio.run(service.add_oauth(ProviderKind.OPENAI, 'plan', flow))
    account = service.accounts.load()[0]
    service.secrets.delete(account.id)

    assert service._api_key_for(account) is None


def test_secret_is_missing_produces_a_domain_error(tmp_path):
    service = _service(tmp_path)
    _add(service)
    account = service.list_accounts()[0]
    FileSecretStore(tmp_path / 'providers' / 'credentials.json').delete(account.id)

    with pytest.raises(CredentialError):
        service.select('work', 'first/model')


def test_api_key_is_stored_as_a_key_secret(tmp_path):
    service = _service(tmp_path)
    _add(service)
    account = service.list_accounts()[0]
    secret = service.secrets.get(account.id)
    assert isinstance(secret, ApiKeySecret)
    assert secret.key == 'sk-test'


def test_apply_restores_reasoning_for_uncached_model(tmp_path):
    service = _service(tmp_path)
    account = _add(service).account
    saved = Selection(
        account_id=account.id, model='custom/unknown', thinking='high', window=123000, efforts=('low', 'high')
    )
    snapshot = service.apply(saved)
    assert snapshot.thinking == 'high'
    assert snapshot.selection.window == 123000
    assert service.state.last_selection().thinking == 'high'
    assert service.set_thinking('low').thinking == 'low'
