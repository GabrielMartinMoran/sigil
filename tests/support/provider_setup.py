"""Provider model and account setup without terminal imports."""

from __future__ import annotations

from pathlib import Path

from src.core.providers.bootstrap import ProviderPaths, build_service
from src.core.providers.catalog.probe import ProbeResult
from src.core.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.core.providers.factory.unconfigured import UnconfiguredModel

CATALOG = ModelCatalog(
    models=(
        ModelCapability(name='first/model', context_window=100_000, efforts=('low', 'high')),
        ModelCapability(name='second/model', context_window=200_000, efforts=('low', 'medium', 'high')),
    ),
    source='remote',
)


class FakeCatalog:
    """Duck-typed ``ModelCatalogService``: no network, deterministic."""

    def __init__(self, catalog: ModelCatalog = CATALOG) -> None:
        self._catalog = catalog
        self._cache: dict[str, ModelCatalog] = {}

    def cached(self, account):
        return self._cache.get(account.id)

    async def models(self, account, *, secret=None):
        self._cache[account.id] = self._catalog
        return self._catalog

    def invalidate(self, account_id=None) -> None:
        if account_id is None:
            self._cache.clear()
        else:
            self._cache.pop(account_id, None)


async def _accept(_account, _key):
    return ProbeResult('valid')


async def _prepared(tmp_path: Path, alias: str = 'work'):
    """A service with one OpenRouter account already configured."""
    service = _service(tmp_path)
    await service.add_api_key(_kind(), alias, 'sk-test')
    return service


def _service(tmp_path: Path, session_id: str = 'session-1'):
    return build_service(
        ProviderPaths(tmp_path / 'providers'),
        session_id=session_id,
        initial_model=UnconfiguredModel(),
        probe=_accept,
        catalog=FakeCatalog(),
        oauth_provider_factory=lambda credential, account: _fake_provider(),
    )


def _fake_provider():
    from pydantic_ai.providers.openai import OpenAIProvider

    return OpenAIProvider(api_key='from-oauth')


def _kind():
    from src.core.providers.domain.provider_kind import ProviderKind

    return ProviderKind.OPENROUTER
