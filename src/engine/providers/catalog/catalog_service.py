"""The catalogue as the rest of the application sees it.

One entry point: give it an account, get a ``ModelCatalog`` back, never an
exception. The remote list is tried first, the static list is the floor, and any
failure is reported in ``ModelCatalog.error`` so the UI can say why the menu is
short instead of pretending everything is fine.
"""

from __future__ import annotations

from src.engine.providers.catalog.catalog_cache import CatalogCache
from src.engine.providers.catalog.http_source import fetch_json as default_fetch_json
from src.engine.providers.catalog.openai_source import OpenAISource
from src.engine.providers.catalog.opencode_source import OpenCodeSource
from src.engine.providers.catalog.openrouter_source import OpenRouterSource
from src.engine.providers.catalog.source import FetchJson, ModelCatalogSource
from src.engine.providers.catalog.static_source import fallback_models
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import CatalogError


class ModelCatalogService:
    """Resolves the model list of an account, with cache and fallback."""

    def __init__(
        self,
        sources: tuple[ModelCatalogSource, ...] | None = None,
        *,
        cache: CatalogCache | None = None,
        fetch_json: FetchJson | None = None,
    ) -> None:
        self._sources = sources or (OpenRouterSource(), OpenCodeSource(), OpenAISource())
        self._cache = cache or CatalogCache()
        self._fetch_json = fetch_json or default_fetch_json

    def cached(self, account: ProviderAccount) -> ModelCatalog | None:
        """The catalogue only if it is already fresh. Never touches the network."""
        return self._cache.get(account.id)

    async def models(self, account: ProviderAccount, *, secret: str | None = None) -> ModelCatalog:
        """The catalogue of ``account``, cached when possible.

        Never raises: a failure is reported as a static catalogue carrying the
        reason, because a model menu that crashes is worse than a short one.
        """
        fresh = self._cache.get(account.id)
        if fresh is not None:
            return fresh

        source = self._source_for(account)
        if source is None:
            return ModelCatalog(models=fallback_models(account), source='static', error=None)

        try:
            models = await source.models(account, secret=secret, fetch_json=self._fetch_json)
        except (CatalogError, OSError, ValueError) as exc:
            return ModelCatalog(models=fallback_models(account), source='static', error=str(exc))
        except Exception as exc:  # noqa: BLE001 - a provider outage is not a crash
            return ModelCatalog(models=fallback_models(account), source='static', error=_describe(exc))

        if not models:
            return ModelCatalog(
                models=fallback_models(account),
                source='static',
                error='The provider returned an empty model list.',
            )

        catalog = ModelCatalog(models=models, source='remote')
        self._cache.put(account.id, catalog)
        return catalog

    def invalidate(self, account_id: str | None = None) -> None:
        self._cache.invalidate(account_id)

    def _source_for(self, account: ProviderAccount) -> ModelCatalogSource | None:
        for source in self._sources:
            if source.supports(account):
                return source
        return None


def _describe(exc: BaseException) -> str:
    """Readable one-liner for a transport error, without a traceback."""
    text = str(exc).strip()
    return text or exc.__class__.__name__


def best_effort_window(models: tuple[ModelCapability, ...], model: str) -> int | None:
    """Window of ``model`` in ``models``, or ``None``."""
    for candidate in models:
        if candidate.name == model:
            return candidate.context_window
    return None
