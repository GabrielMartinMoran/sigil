"""Tests for the provider accounts, aliases and selections (domain layer)."""

from __future__ import annotations

import pytest

from src.engine.providers.domain.auth_method import AuthMethod, auth_methods_for
from src.engine.providers.domain.model_capability import (
    ModelCapability,
    ModelCatalog,
    normalize_efforts,
)
from src.engine.providers.domain.provider_account import (
    ALIAS_MAX_LENGTH,
    ProviderAccount,
    normalize_alias,
)
from src.engine.providers.domain.provider_error import AliasError
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant, variants_for
from src.engine.providers.domain.selection import Selection
from src.engine.providers.domain.thinking import (
    is_effort_level,
    label,
    parse_choice,
    settings_value,
)


def test_normalize_alias_collapses_inner_whitespace():
    assert normalize_alias('  work   openai  ') == 'work openai'


@pytest.mark.parametrize('raw', ['', '   ', '-leading', '.dot', 'a' * (ALIAS_MAX_LENGTH + 1)])
def test_normalize_alias_rejects_bad_values(raw):
    with pytest.raises(AliasError):
        normalize_alias(raw)


def test_normalize_alias_accepts_the_documented_characters():
    assert normalize_alias('Work_Key.2-x 1') == 'Work_Key.2-x 1'


def test_account_create_assigns_an_opaque_id_and_normalizes_the_alias():
    account = ProviderAccount.create(' Work ', ProviderKind.OPENROUTER)
    assert len(account.id) == 32
    assert account.alias == 'Work'
    assert account.kind is ProviderKind.OPENROUTER
    assert account.variant is ProviderVariant.DEFAULT
    assert account.auth is AuthMethod.API_KEY


def test_account_display_name_shows_kind_and_variant():
    account = ProviderAccount.create('work', ProviderKind.OPENCODE, ProviderVariant.GO)
    assert account.provider_label == 'OpenCode Go'
    assert account.display_name == 'work · OpenCode Go'


def test_account_round_trips_through_dict_without_losing_fields():
    account = ProviderAccount.create('work', ProviderKind.OPENAI, auth=AuthMethod.OAUTH).with_model('gpt-5')
    restored = ProviderAccount.from_dict(account.to_dict())
    assert restored == account


def test_account_dict_has_no_secret_fields():
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER)
    payload = account.to_dict()
    assert set(payload) == {'id', 'alias', 'kind', 'variant', 'auth', 'created_at', 'last_model'}


def test_account_from_dict_tolerates_missing_optional_fields():
    restored = ProviderAccount.from_dict({'id': 'x', 'alias': 'a', 'kind': 'openrouter'})
    assert restored.variant is ProviderVariant.DEFAULT
    assert restored.auth is AuthMethod.API_KEY
    assert restored.last_model is None


def test_rename_keeps_the_identity_and_the_model():
    account = ProviderAccount.create('before', ProviderKind.OPENROUTER).with_model('m')
    renamed = account.renamed('after')
    assert renamed.id == account.id
    assert renamed.alias == 'after'
    assert renamed.last_model == 'm'


def test_variants_and_auth_methods_depend_on_the_kind():
    assert variants_for(ProviderKind.OPENCODE) == (ProviderVariant.ZEN, ProviderVariant.GO)
    assert variants_for(ProviderKind.OPENROUTER) == (ProviderVariant.DEFAULT,)
    assert auth_methods_for(ProviderKind.OPENAI) == (AuthMethod.API_KEY, AuthMethod.OAUTH)
    assert auth_methods_for(ProviderKind.OPENCODE) == (AuthMethod.API_KEY,)


def test_selection_round_trips_and_rejects_incomplete_data():
    selection = Selection(account_id='abc', model='anthropic/x', thinking='high')
    assert Selection.from_dict(selection.to_dict()) == selection
    assert Selection.from_dict({}) is None
    assert Selection.from_dict({'account_id': 'abc'}) is None
    assert Selection.from_dict({'account_id': 'abc', 'model': 'x', 'thinking': None}).thinking == 'default'


def test_selection_transitions_keep_the_other_fields():
    selection = Selection(account_id='a', model='m', thinking='low')
    assert selection.with_model('n').model == 'n'
    assert selection.with_model('n').thinking == 'low'
    assert selection.with_thinking('max').model == 'm'


def test_parse_choice_accepts_levels_and_the_two_special_values():
    assert parse_choice(' HIGH ') == 'high'
    assert parse_choice('default') == 'default'
    assert parse_choice('off') == 'off'
    assert parse_choice('ludicrous') is None


def test_settings_value_maps_default_to_nothing_and_off_to_false():
    assert settings_value('default') is None
    assert settings_value('off') is False
    assert settings_value('medium') == 'medium'
    assert label('default') == 'default'


def test_is_effort_level_only_accepts_real_levels():
    assert is_effort_level('xhigh')
    assert not is_effort_level('default')
    assert not is_effort_level('off')


def test_normalize_efforts_drops_unknown_levels_and_orders_them():
    assert normalize_efforts(['high', 'bogus', 'minimal', 'high']) == ('minimal', 'high')
    assert normalize_efforts(None) == ()
    assert normalize_efforts([]) == ()


def test_capability_labels():
    model = ModelCapability(name='m', context_window=200_000, efforts=('low', 'high'))
    assert model.window_label == '200k'
    assert model.efforts_label == 'low/high'
    assert model.allows('low')
    assert not model.allows('max')

    unknown = ModelCapability(name='m')
    assert unknown.window_label == '?'
    assert unknown.efforts_label == 'no effort control'
    assert not unknown.allows('low')


def test_capability_window_label_handles_millions():
    assert ModelCapability(name='m', context_window=1_000_000).window_label == '1M'


def test_catalog_find_and_failure_state():
    model = ModelCapability(name='a/b')
    catalog = ModelCatalog(models=(model,), source='remote')
    assert catalog.find('a/b') is model
    assert catalog.find('missing') is None
    failed = catalog.failed('offline')
    assert failed.find('a/b') is model
    assert failed.source == 'static'
    assert failed.error == 'offline'
    assert not ModelCatalog().is_empty is False
