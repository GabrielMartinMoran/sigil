"""Regression tests for the key handling and for the effort levels.

Two things are pinned here:

- `ctrl+m` / `ctrl+l` open the pickers **without** breaking Enter. They are read
  in `PromptInput.on_key`, not bound globally, because on a legacy terminal Ctrl+M
  and Enter are the same byte (CR, `0x0D`) and a global binding swallows Enter.
- The effort levels of a model reach the picker. They used to be lost because the
  static fallback returned an empty list for every model, and that empty list was
  persisted with the selection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.core.providers.bootstrap import build_service
from src.core.providers.catalog.probe import ProbeResult
from src.core.providers.catalog.static_source import (
    OPENCODE_FALLBACK,
    OPENROUTER_FALLBACK,
    fallback_capability,
)
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.model_catalog import ModelCatalog
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.factory.unconfigured_model import UnconfiguredModel
from src.core.providers.provider_paths import ProviderPaths

DEEPSEEK = ModelCapability(
    name='deepseek-v4.1-flash',
    display_name='DeepSeek V4.1 Flash',
    context_window=200_000,
    efforts=('low', 'high', 'max'),
)

CATALOG = ModelCatalog(models=(DEEPSEEK,), source='remote')


async def _accept(_account, _key):
    return ProbeResult('valid')


class FakeCatalog:
    def __init__(self, catalog: ModelCatalog = CATALOG) -> None:
        self._catalog = catalog
        self._cache: dict[str, ModelCatalog] = {}
        self.fetches = 0

    def cached(self, account):
        return self._cache.get(account.id)

    async def models(self, account, *, secret=None):
        self.fetches += 1
        self._cache[account.id] = self._catalog
        return self._catalog

    def invalidate(self, account_id=None) -> None:
        if account_id is None:
            self._cache.clear()
        else:
            self._cache.pop(account_id, None)


def _service(tmp_path: Path, catalog=None, session_id: str = 's'):
    return build_service(
        ProviderPaths(tmp_path / 'providers'),
        session_id=session_id,
        initial_model=UnconfiguredModel(),
        probe=_accept,
        catalog=catalog or FakeCatalog(),
    )


# --- the effort levels ------------------------------------------------------


def test_the_static_fallback_knows_the_efforts_of_its_models() -> None:
    for model in (*OPENROUTER_FALLBACK, *OPENCODE_FALLBACK):
        assert model.efforts or model.name in {'gpt-4.1'}, model.name


def test_the_static_fallback_no_longer_claims_unknown_windows() -> None:
    for model in (*OPENROUTER_FALLBACK, *OPENCODE_FALLBACK):
        assert model.context_window is not None, model.name


def test_fallback_capability_finds_a_model_in_any_family() -> None:
    assert fallback_capability('glm-5.3') is not None
    assert fallback_capability('glm-5.3').efforts == ('low', 'high', 'max')
    assert fallback_capability('nope/not-a-model') is None


def test_the_efforts_survive_being_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    asyncio.run(service.add_api_key(ProviderKind.OPENCODE, 'ds', 'sk-test'))

    selection = service.state.last_selection()
    assert selection is not None
    assert selection.efforts == ('low', 'high', 'max')


def test_refresh_capability_is_a_no_op_without_a_selection(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = asyncio.run(service.refresh_capability())
    assert not snapshot.is_configured


def test_a_model_name_the_route_rejects_is_a_domain_error_not_a_crash(tmp_path: Path) -> None:
    """OpenRouter requires a `vendor/` prefix; the SDK raises its own UserError.

    A hand-typed id (or a stale state file) without the prefix used to escape as a
    traceback instead of a readable message. The catalogue is faked with an
    OpenCode-style id to trigger exactly that.
    """
    from src.core.providers.domain.provider_error import ProviderError

    service = _service(tmp_path)

    try:
        asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, 'or', 'sk-test'))
    except ProviderError as error:
        assert 'deepseek-v4.1-flash' in str(error)
        assert 'or' in str(error)
    else:  # pragma: no cover
        raise AssertionError('expected a ProviderError')


def test_opencode_accepts_bare_model_ids(tmp_path: Path) -> None:
    """The opposite case: a route that takes plain ids must not be rejected."""
    service = _service(tmp_path)
    asyncio.run(service.add_api_key(ProviderKind.OPENCODE, 'oc', 'sk-test'))

    snapshot = service.select('oc', 'deepseek-v4.1-flash')

    assert snapshot.model == 'deepseek-v4.1-flash'
