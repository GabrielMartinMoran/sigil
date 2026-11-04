"""Tests for the provider stores: atomicity, permissions and secret redaction."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import AliasError, CredentialError
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.secret import ApiKeySecret, OAuthSecret
from src.engine.providers.domain.selection import Selection
from src.engine.providers.store.account_store import FileAccountStore
from src.engine.providers.store.atomic import MODE_PRIVATE, read_json, write_json
from src.engine.providers.store.secret_store import FileSecretStore
from src.engine.providers.store.state_store import FileStateStore


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- atomic -----------------------------------------------------------------


def test_write_json_creates_the_parent_directory(tmp_path):
    target = tmp_path / 'nested' / 'file.json'
    write_json(target, {'a': 1})
    assert read_json(target) == {'a': 1}


def test_write_json_applies_the_private_mode(tmp_path):
    target = tmp_path / 'secret.json'
    write_json(target, {'a': 1}, mode=MODE_PRIVATE)
    assert _mode(target) == MODE_PRIVATE


def test_write_json_leaves_no_temporary_file(tmp_path):
    target = tmp_path / 'file.json'
    write_json(target, {'a': 1})
    assert [p.name for p in tmp_path.iterdir()] == ['file.json']


def test_write_json_tightens_a_pre_existing_world_readable_file(tmp_path):
    target = tmp_path / 'secret.json'
    target.write_text('{}', encoding='utf-8')
    os.chmod(target, 0o666)
    write_json(target, {'a': 1}, mode=MODE_PRIVATE)
    assert _mode(target) == MODE_PRIVATE


@pytest.mark.parametrize('payload', ['not json', '[1, 2]', ''])
def test_read_json_returns_none_for_anything_unusable(tmp_path, payload):
    target = tmp_path / 'file.json'
    target.write_text(payload, encoding='utf-8')
    assert read_json(target) is None


def test_read_json_returns_none_for_a_missing_file(tmp_path):
    assert read_json(tmp_path / 'nope.json') is None


# --- secrets ----------------------------------------------------------------


def test_api_key_secret_hides_the_key_from_repr_and_str():
    secret = ApiKeySecret(key='sk-super-secret', alias='work')
    assert 'sk-super-secret' not in repr(secret)
    assert 'sk-super-secret' not in str(secret)
    assert 'work' in repr(secret)


def test_oauth_secret_hides_the_tokens_but_shows_the_client_and_scope():
    secret = OAuthSecret(
        access_token='ACCESS',
        refresh_token='REFRESH',
        client_id='client-1',
        id_token='ID',
        scope='openid chatgpt.tokens.use.direct',
        alias='plan',
    )
    text = repr(secret)
    for hidden in ('ACCESS', 'REFRESH', 'ID'):
        assert hidden not in text
    assert 'client-1' in text
    assert secret.plan_usage_granted


def test_oauth_secret_reports_a_missing_plan_usage_grant():
    secret = OAuthSecret(access_token='a', refresh_token='r', scope='openid')
    assert not secret.plan_usage_granted


def test_secret_store_round_trips_an_api_key(tmp_path):
    store = FileSecretStore(tmp_path / 'credentials.json')
    store.put('acct-1', ApiKeySecret(key='sk-1'))
    assert store.get('acct-1') == ApiKeySecret(key='sk-1')


def test_secret_store_round_trips_an_oauth_credential(tmp_path):
    store = FileSecretStore(tmp_path / 'credentials.json')
    secret = OAuthSecret(access_token='a', refresh_token='r', client_id='c', id_token='i', scope='s')
    store.put('acct-1', secret)
    assert store.get('acct-1') == secret


def test_secret_store_file_is_private_and_unique(tmp_path):
    path = tmp_path / 'credentials.json'
    store = FileSecretStore(path)
    store.put('acct-1', ApiKeySecret(key='sk-1'))
    assert _mode(path) == MODE_PRIVATE
    assert 'sk-1' in path.read_text(encoding='utf-8')


def test_secret_store_tightens_a_restored_file(tmp_path):
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps({'acct-1': {'type': 'api_key', 'key': 'sk-1'}}), encoding='utf-8')
    os.chmod(path, 0o644)
    assert FileSecretStore(path).get('acct-1') == ApiKeySecret(key='sk-1')
    assert _mode(path) == MODE_PRIVATE


def test_secret_store_ignores_unknown_or_broken_entries(tmp_path):
    path = tmp_path / 'credentials.json'
    path.write_text(
        json.dumps(
            {
                'a': {'type': 'unknown'},
                'b': {'type': 'api_key'},
                'c': {'type': 'oauth', 'access_token': 'x'},
            }
        ),
        encoding='utf-8',
    )
    store = FileSecretStore(path)
    assert store.get('a') is None
    assert store.get('b') is None
    assert store.get('c') is None
    assert store.get('missing') is None


def test_secret_store_delete_removes_only_that_account(tmp_path):
    store = FileSecretStore(tmp_path / 'credentials.json')
    store.put('a', ApiKeySecret(key='1'))
    store.put('b', ApiKeySecret(key='2'))
    store.delete('a')
    store.delete('nope')
    assert store.get('a') is None
    assert store.get('b') == ApiKeySecret(key='2')


def test_secret_store_reports_a_write_failure_as_a_domain_error(tmp_path):
    blocker = tmp_path / 'blocked'
    blocker.write_text('', encoding='utf-8')
    store = FileSecretStore(blocker / 'credentials.json')
    with pytest.raises(CredentialError):
        store.put('a', ApiKeySecret(key='1'))


# --- accounts ---------------------------------------------------------------


def test_account_store_round_trips_and_preserves_order(tmp_path):
    store = FileAccountStore(tmp_path / 'accounts.json')
    first = ProviderAccount.create('one', ProviderKind.OPENROUTER)
    second = ProviderAccount.create('two', ProviderKind.OPENAI)
    store.save([first, second])
    assert store.load() == (first, second)


def test_account_store_returns_empty_when_the_file_is_missing(tmp_path):
    assert FileAccountStore(tmp_path / 'accounts.json').load() == ()


def test_account_store_skips_corrupt_entries_without_losing_the_rest(tmp_path):
    path = tmp_path / 'accounts.json'
    good = ProviderAccount.create('good', ProviderKind.OPENROUTER).to_dict()
    path.write_text(
        json.dumps({'accounts': [good, {'id': 'x'}, 'nonsense', {'kind': 'unknown'}]}),
        encoding='utf-8',
    )
    assert [account.alias for account in FileAccountStore(path).load()] == ['good']


def test_account_store_rejects_unknown_kinds_at_load_time(tmp_path):
    path = tmp_path / 'accounts.json'
    path.write_text(json.dumps({'accounts': [{'id': 'x', 'alias': 'a', 'kind': 'gemini'}]}), encoding='utf-8')
    assert FileAccountStore(path).load() == ()


def test_account_store_lookup_is_case_insensitive(tmp_path):
    store = FileAccountStore(tmp_path / 'accounts.json')
    account = ProviderAccount.create('Work', ProviderKind.OPENROUTER)
    store.save([account])
    assert store.find_alias('work') == account
    assert store.find_alias('WORK') == account
    assert store.find_id(account.id) == account
    assert store.find_alias('other') is None


def test_account_store_alias_uniqueness_ignores_the_account_itself(tmp_path):
    store = FileAccountStore(tmp_path / 'accounts.json')
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER)
    store.save([account])
    store.ensure_free_alias('work', ignore_id=account.id)
    with pytest.raises(AliasError):
        store.ensure_free_alias('WORK')


# --- state ------------------------------------------------------------------


def test_state_store_starts_empty_and_survives_a_restart(tmp_path):
    path = tmp_path / 'state.json'
    store = FileStateStore(path)
    assert store.last_selection() is None

    selection = Selection(account_id='a', model='m', thinking='high')
    store.set_last_selection(selection)
    assert FileStateStore(path).last_selection() == selection


def test_state_store_can_forget_the_selection(tmp_path):
    store = FileStateStore(tmp_path / 'state.json')
    store.set_last_selection(Selection(account_id='a', model='m'))
    store.set_last_selection(None)
    assert store.last_selection() is None


def test_state_store_forget_only_when_the_account_matches(tmp_path):
    store = FileStateStore(tmp_path / 'state.json')
    store.set_last_selection(Selection(account_id='a', model='m'))
    store.forget('b')
    assert store.last_selection() is not None
    store.forget('a')
    assert store.last_selection() is None


def test_state_store_installation_id_is_stable_and_well_formed(tmp_path):
    path = tmp_path / 'state.json'
    first = FileStateStore(path).installation_id()
    second = FileStateStore(path).installation_id()
    assert first == second
    assert first.startswith('urn:uuid:')


def test_state_store_survives_an_unwritable_location(tmp_path):
    blocker = tmp_path / 'blocked'
    blocker.write_text('', encoding='utf-8')
    store = FileStateStore(blocker / 'state.json')
    store.set_last_selection(Selection(account_id='a', model='m'))
    assert store.save_error is not None
    # The in-memory value is still usable for the running process.
    assert store.last_selection() == Selection(account_id='a', model='m')
