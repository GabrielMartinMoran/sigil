"""Tests for the credential probe: which answers mean what.

The mapping is the whole point of the module, so it is tested without HTTP.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.engine.providers.catalog.probe import (
    ProbeResult,
    _classify,
    probe_credential,
)
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant


def _account(kind: ProviderKind, variant=ProviderVariant.DEFAULT) -> ProviderAccount:
    return ProviderAccount.create(f'{kind}-{variant}', kind, variant)


@pytest.mark.parametrize('status', [401, 403])
def test_auth_statuses_are_a_rejection(status):
    assert _classify(status, '{"error": "nope"}').outcome == 'rejected'


@pytest.mark.parametrize('status', [200, 201, 204])
def test_success_is_valid(status):
    assert _classify(status, '').outcome == 'valid'


@pytest.mark.parametrize('status', [400, 404, 429, 500, 502])
def test_other_statuses_are_undecided(status):
    assert _classify(status, 'boom').outcome == 'unknown'


def test_the_detail_is_shortened():
    result = _classify(401, 'x' * 5000)
    assert len(result.detail) <= 160


def test_probe_result_predicates():
    assert ProbeResult('valid').is_valid
    assert ProbeResult('rejected').is_rejected
    assert not ProbeResult('unknown').is_valid
    assert not ProbeResult('unknown').is_rejected


def test_a_network_failure_is_undecided_not_a_rejection(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise httpx.ConnectError('no route to host')

    monkeypatch.setattr(httpx.AsyncClient, 'get', boom)

    result = asyncio.run(probe_credential(_account(ProviderKind.OPENROUTER), 'sk-test'))
    assert result.outcome == 'unknown'
    assert 'no route' in result.detail


def test_openrouter_probe_hits_the_key_endpoint(monkeypatch):
    seen: list[str] = []

    async def fake_get(self, url, headers=None):
        seen.append(url)
        return httpx.Response(200, json={'data': {}})

    monkeypatch.setattr(httpx.AsyncClient, 'get', fake_get)
    asyncio.run(probe_credential(_account(ProviderKind.OPENROUTER), 'sk-test'))

    assert seen == ['https://openrouter.ai/api/v1/key']


def test_openai_probe_hits_the_models_endpoint(monkeypatch):
    seen: list[str] = []

    async def fake_get(self, url, headers=None):
        seen.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, 'get', fake_get)
    asyncio.run(probe_credential(_account(ProviderKind.OPENAI), 'sk-test'))

    assert seen == ['https://api.openai.com/v1/models']


def test_zen_probe_hits_the_public_catalogue(monkeypatch):
    seen: list[str] = []

    async def fake_get(self, url, headers=None):
        seen.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, 'get', fake_get)
    asyncio.run(probe_credential(_account(ProviderKind.OPENCODE, ProviderVariant.ZEN), 'sk-test'))

    assert seen == ['https://opencode.ai/zen/v1/models']


def test_go_probe_posts_a_one_token_completion_because_go_has_no_catalogue(monkeypatch):
    seen: list[dict] = []

    async def fake_post(self, url, headers=None, json=None):
        seen.append({'url': url, 'headers': headers, 'body': json})
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, 'post', fake_post)
    asyncio.run(probe_credential(_account(ProviderKind.OPENCODE, ProviderVariant.GO), 'sk-test'))

    assert seen[0]['url'].endswith('/inference/go/openai/v1/chat/completions')
    assert seen[0]['headers']['x-opencode-session']
    assert seen[0]['body']['max_tokens'] == 1


@pytest.mark.parametrize('status', [400, 429])
def test_go_treats_a_routing_or_quota_error_as_a_live_key(monkeypatch, status):
    """`400 Model is unavailable` and `429 FreeUsageLimitError` are not a bad key."""

    async def fake_post(self, url, headers=None, json=None):
        return httpx.Response(status, text='{"error": {"message": "Model is unavailable"}}')

    monkeypatch.setattr(httpx.AsyncClient, 'post', fake_post)
    result = asyncio.run(probe_credential(_account(ProviderKind.OPENCODE, ProviderVariant.GO), 'sk-test'))

    assert result.outcome == 'valid'


def test_go_still_reports_a_rejected_key(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        return httpx.Response(401, text='{"error": {"message": "Invalid credential"}}')

    monkeypatch.setattr(httpx.AsyncClient, 'post', fake_post)
    result = asyncio.run(probe_credential(_account(ProviderKind.OPENCODE, ProviderVariant.GO), 'sk-test'))

    assert result.outcome == 'rejected'
    assert 'Invalid credential' in result.detail
