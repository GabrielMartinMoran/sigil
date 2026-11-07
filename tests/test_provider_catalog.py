"""Tests for the model catalogue: parsers, cache and fallback behaviour.

Every test runs without network: the parsers take a payload and the service takes
an injected ``fetch_json``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.engine.providers.catalog.catalog_cache import CatalogCache
from src.engine.providers.catalog.catalog_service import ModelCatalogService, best_effort_window
from src.engine.providers.catalog.openai_source import OpenAISource
from src.engine.providers.catalog.openai_source import parse_models as parse_openai
from src.engine.providers.catalog.opencode_source import parse_entry as parse_opencode_entry
from src.engine.providers.catalog.opencode_source import parse_models as parse_opencode
from src.engine.providers.catalog.openrouter_source import parse_models as parse_openrouter
from src.engine.providers.catalog.static_source import fallback_models
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant

FIXTURES = Path(__file__).parent / 'fixtures'


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def _account(kind: ProviderKind, variant=ProviderVariant.DEFAULT) -> ProviderAccount:
    return ProviderAccount.create(f'{kind}-{variant}', kind, variant)


# --- OpenRouter -------------------------------------------------------------


def test_openrouter_parses_window_efforts_and_cleans_the_name():
    models = parse_openrouter(_fixture('openrouter_models.json'))
    by_name = {model.name: model for model in models}

    sonnet = by_name['anthropic/claude-sonnet-4.6']
    assert sonnet.display_name == 'Claude Sonnet 4.6'
    assert sonnet.context_window == 1_000_000
    assert sonnet.efforts == ('low', 'medium', 'high', 'max')
    assert sonnet.dialect == 'chat-completions'
    assert sonnet.supports_tools


def test_openrouter_reports_no_efforts_when_the_model_does_not_reason():
    by_name = {model.name: model for model in parse_openrouter(_fixture('openrouter_models.json'))}
    assert by_name['openai/gpt-4o-mini'].efforts == ()


def test_openrouter_marks_a_model_without_tool_support():
    by_name = {model.name: model for model in parse_openrouter(_fixture('openrouter_models.json'))}
    assert not by_name['meta/llama-free'].supports_tools


def test_openrouter_keeps_a_model_with_no_window_rather_than_dropping_it():
    by_name = {model.name: model for model in parse_openrouter(_fixture('openrouter_models.json'))}
    assert by_name['meta/llama-free'].context_window is None


def test_openrouter_skips_entries_with_a_non_string_id():
    names = [model.name for model in parse_openrouter(_fixture('openrouter_models.json'))]
    assert len(names) == 4
    assert all(isinstance(name, str) for name in names)


def test_openrouter_reads_a_malformed_window_as_unknown():
    by_name = {model.name: model for model in parse_openrouter(_fixture('openrouter_models.json'))}
    assert by_name['meta/llama-free'].context_window is None


# --- OpenCode ---------------------------------------------------------------


def test_opencode_reads_the_dialect_and_endpoint_from_the_api_map():
    models = parse_opencode(_fixture('opencode_models.json'))
    by_name = {model.name: model for model in models}

    claude = by_name['claude-sonnet-4-6']
    assert claude.dialect == 'messages'
    assert claude.base_url == 'https://opencode.ai/inference/anthropic/v1'
    assert claude.context_window == 1_000_000

    gpt = by_name['gpt-5']
    assert gpt.dialect == 'responses'
    assert gpt.base_url == 'https://opencode.ai/inference/openai/v1'


def test_opencode_prefers_chat_completions_when_a_model_offers_several():
    entry = {
        'id': 'multi',
        'api': {
            'messages': {'base_url': 'https://example/anthropic/v1'},
            'responses': {'base_url': 'https://example/openai/v1'},
            'chat-completions': {'base_url': 'https://example/openai/v1'},
        },
    }
    assert parse_opencode_entry(entry).dialect == 'chat-completions'


def test_opencode_parses_effort_levels_from_capabilities():
    models = parse_opencode(_fixture('opencode_models.json'))
    by_name = {model.name: model for model in models}
    assert by_name['claude-sonnet-4-6'].efforts == ('low', 'medium', 'high', 'max')
    assert by_name['gpt-5-nano'].efforts == ('minimal', 'low', 'medium', 'high')


def test_opencode_without_capabilities_offers_no_efforts():
    entry = {'id': 'plain', 'api': {'chat-completions': {'base_url': 'https://x/v1'}}}
    model = parse_opencode_entry(entry)
    assert model.efforts == ()
    assert model.supports_tools


def test_opencode_reads_tool_support():
    entry = {
        'id': 'notools',
        'api': {'chat-completions': {'base_url': 'https://x/v1'}},
        'capabilities': {'tools': {'supported': False}},
    }
    assert not parse_opencode_entry(entry).supports_tools


def test_opencode_falls_back_to_chat_completions_without_an_api_map():
    model = parse_opencode_entry({'id': 'bare'})
    assert model.dialect == 'chat-completions'
    assert model.base_url is None
    assert model.context_window is None


# --- OpenAI -----------------------------------------------------------------


def _fake_profile(name: str):
    table = {
        'gpt-5.1': {
            'context_window': 400_000,
            'supports_thinking': True,
            'openai_reasoning_efforts': frozenset({'low', 'medium', 'high'}),
        },
        'gpt-4.1': {'context_window': 1_000_000, 'supports_thinking': False},
        'o3': {'supports_thinking': True},
    }
    return table.get(name)


def test_openai_hides_the_families_that_cannot_hold_a_chat():
    names = [model.name for model in parse_openai(_fixture('openai_models.json'), _fake_profile)]
    assert names == ['gpt-4.1', 'gpt-5.1', 'o3']


def test_openai_takes_the_window_and_efforts_from_the_profile():
    by_name = {model.name: model for model in parse_openai(_fixture('openai_models.json'), _fake_profile)}
    assert by_name['gpt-5.1'].context_window == 400_000
    assert by_name['gpt-5.1'].efforts == ('low', 'medium', 'high')
    assert by_name['gpt-4.1'].efforts == ()
    assert by_name['gpt-4.1'].dialect == 'responses'


def test_openai_tolerates_a_model_with_no_profile():
    by_name = {model.name: model for model in parse_openai(_fixture('openai_models.json'), lambda _name: None)}
    assert by_name['o3'].context_window is None


# --- OpenAI, the plan shape -------------------------------------------------
#
# A signed-in ChatGPT account answers a different endpoint shape: `{"models": [...]}`
# with `slug`, `display_name`, `context_window`, `input_modalities` and
# `supported_reasoning_levels` — everything, under other names. Reading only
# `{"data": [{"id": ...}]}` made every plan account fall back to four hardcoded
# models with "the provider returned an empty model list" next to them.


def test_the_plan_payload_is_read_even_though_it_has_no_data_key():
    names = [model.name for model in parse_openai(_fixture('openai_plan_models.json'), lambda _name: None)]
    assert names == ['codex-auto-review', 'gpt-6.1-sol', 'gpt-reserve']


def test_the_plan_payload_hides_the_families_that_cannot_hold_a_chat():
    names = [model.name for model in parse_openai(_fixture('openai_plan_models.json'), lambda _name: None)]
    assert 'gpt-image-1' not in names
    assert 'text-embedding-3-large' not in names


def test_the_plan_payload_supplies_the_display_name():
    by_name = {model.name: model for model in parse_openai(_fixture('openai_plan_models.json'), lambda _name: None)}
    assert by_name['gpt-6.1-sol'].label == 'GPT-6.1-Sol'
    assert by_name['gpt-6.1-sol'].name == 'gpt-6.1-sol'


def test_the_plan_window_is_the_usable_one_not_the_ceiling():
    """`max_context_window` is reachable only with experimental context; budgeting it
    would make the meter promise a turn the provider is going to truncate."""
    by_name = {model.name: model for model in parse_openai(_fixture('openai_plan_models.json'), lambda _name: None)}
    assert by_name['gpt-6.1-sol'].context_window == 500_000


def test_the_plan_reasoning_levels_are_objects_and_still_produce_efforts():
    """`[{"effort": "low"}]` stringified is not `low`, so reading them as strings left
    every plan model looking like it cannot reason."""
    by_name = {model.name: model for model in parse_openai(_fixture('openai_plan_models.json'), lambda _name: None)}
    assert by_name['gpt-6.1-sol'].efforts == ('low', 'medium', 'high', 'xhigh')
    assert by_name['gpt-reserve'].efforts == ()


def test_the_plan_modalities_decide_image_input():
    by_name = {model.name: model for model in parse_openai(_fixture('openai_plan_models.json'), lambda _name: None)}
    assert by_name['gpt-6.1-sol'].supports_images is True
    assert by_name['gpt-reserve'].supports_images is False


def test_the_plan_payload_wins_over_the_bundled_profile():
    """Both know the model; the endpoint is the fresher one."""
    profiles = {'gpt-6.1-sol': {'context_window': 128_000, 'supports_thinking': False}}
    by_name = {model.name: model for model in parse_openai(_fixture('openai_plan_models.json'), profiles.get)}
    assert by_name['gpt-6.1-sol'].context_window == 500_000
    assert by_name['gpt-6.1-sol'].efforts == ('low', 'medium', 'high', 'xhigh')


def test_the_plan_payload_picks_up_an_entry_that_names_itself_with_id():
    """`slug` today, `id` tomorrow: accepting both costs nothing and survives the day
    the endpoint changes its mind."""
    payload = {'models': [{'id': 'legacy-model', 'context_window': 128_000}]}
    models = parse_openai(payload, lambda _name: None)
    assert [model.name for model in models] == ['legacy-model']


# --- cache ------------------------------------------------------------------


def test_cache_returns_nothing_past_the_ttl():
    now = [0.0]
    cache = CatalogCache(ttl=10.0)
    cache._clock = lambda: now[0]
    cache.put('a', _catalog('remote'))
    assert cache.get('a') is not None
    now[0] = 9.9
    assert cache.get('a') is not None
    now[0] = 10.0
    assert cache.get('a') is None


def test_cache_invalidation():
    cache = CatalogCache()
    cache.put('a', _catalog('one'))
    cache.put('b', _catalog('two'))
    cache.invalidate('a')
    assert cache.get('a') is None
    assert cache.get('b') is not None
    cache.invalidate()
    assert cache.get('b') is None


def _catalog(tag: str):
    from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog

    return ModelCatalog(models=(ModelCapability(name=tag),), source='remote')


# --- service ----------------------------------------------------------------


def test_service_uses_the_remote_list_and_caches_it():
    asyncio.run(_test_service_uses_the_remote_list_and_caches_it())


async def _test_service_uses_the_remote_list_and_caches_it():
    calls: list[str] = []

    async def fetch(url, headers, timeout):
        calls.append(url)
        return _fixture('openrouter_models.json')

    service = ModelCatalogService(fetch_json=fetch)
    account = _account(ProviderKind.OPENROUTER)

    first = await service.models(account)
    assert first.source == 'remote'
    assert len(first.models) == 4

    second = await service.models(account)
    assert second is first
    assert len(calls) == 1


def test_service_falls_back_to_static_on_a_transport_error():
    asyncio.run(_test_service_falls_back_to_static_on_a_transport_error())


async def _test_service_falls_back_to_static_on_a_transport_error():
    async def fetch(url, headers, timeout):
        raise OSError('network is down')

    service = ModelCatalogService(fetch_json=fetch)
    account = _account(ProviderKind.OPENROUTER)
    catalog = await service.models(account)

    assert catalog.source == 'static'
    assert catalog.error is not None
    assert catalog.models == fallback_models(account)


def test_service_falls_back_when_the_payload_is_unusable():
    asyncio.run(_test_service_falls_back_when_the_payload_is_unusable())


async def _test_service_falls_back_when_the_payload_is_unusable():
    async def fetch(url, headers, timeout):
        return {'nonsense': True}

    service = ModelCatalogService(fetch_json=fetch)
    catalog = await service.models(_account(ProviderKind.OPENCODE))

    assert catalog.source == 'static'
    assert 'data' in (catalog.error or '')


def test_service_reports_an_empty_remote_list_as_a_failure():
    asyncio.run(_test_service_reports_an_empty_remote_list_as_a_failure())


async def _test_service_reports_an_empty_remote_list_as_a_failure():
    async def fetch(url, headers, timeout):
        return {'data': []}

    service = ModelCatalogService(fetch_json=fetch)
    catalog = await service.models(_account(ProviderKind.OPENROUTER))

    assert catalog.source == 'static'
    assert catalog.error == 'The provider returned an empty model list.'


def test_service_never_raises_on_an_unexpected_error():
    asyncio.run(_test_service_never_raises_on_an_unexpected_error())


async def _test_service_never_raises_on_an_unexpected_error():
    async def fetch(url, headers, timeout):
        raise RuntimeError('boom')

    service = ModelCatalogService(fetch_json=fetch)
    catalog = await service.models(_account(ProviderKind.OPENROUTER))

    assert catalog.source == 'static'
    assert catalog.error == 'boom'


def test_service_does_not_send_a_key_to_the_openai_list_without_one():
    asyncio.run(_test_service_does_not_send_a_key_to_the_openai_list_without_one())


async def _test_service_does_not_send_a_key_to_the_openai_list_without_one():
    calls: list[str] = []

    async def fetch(url, headers, timeout):
        calls.append(url)
        return _fixture('openai_models.json')

    def profile(_name: str):
        return None

    sources = (OpenAISource(profile_lookup=profile),)
    service = ModelCatalogService(sources=sources, fetch_json=fetch)
    catalog = await service.models(_account(ProviderKind.OPENAI), secret=None)

    assert calls == []
    assert catalog.source == 'static'


def test_best_effort_window_reads_the_matching_model():
    models = parse_openrouter(_fixture('openrouter_models.json'))
    assert best_effort_window(models, 'anthropic/claude-haiku-4.5') == 200_000
    assert best_effort_window(models, 'missing') is None


def test_static_fallback_covers_every_kind():
    for kind in ProviderKind:
        assert fallback_models(_account(kind))
