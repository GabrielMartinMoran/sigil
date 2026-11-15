"""Shared provider service test support."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.core.providers.bootstrap import ProviderPaths, build_service
from src.core.providers.catalog.catalog_service import ModelCatalogService
from src.core.providers.catalog.probe import ProbeResult
from src.core.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.provider_variant import ProviderVariant
from src.core.providers.domain.secret import OAuthSecret
from src.core.providers.factory.unconfigured import UnconfiguredModel
from src.core.providers.service import ModelService

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
