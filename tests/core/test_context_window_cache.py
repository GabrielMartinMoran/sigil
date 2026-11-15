"""Previously observed model windows survive incomplete catalogues and restarts."""

import asyncio
from pathlib import Path

from src.core.providers.domain.model_capability import ModelCapability, ModelCatalog
from tests.support.provider_service import _add, _seed_catalog, _service


def test_catalogue_windows_survive_restart_and_incomplete_refresh(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _add(service)
    service.select('work', 'second/model')

    restarted = _service(tmp_path)
    incomplete = ModelCatalog(models=(ModelCapability('first/model'), ModelCapability('second/model')), source='remote')
    _seed_catalog(restarted, incomplete)
    asyncio.run(restarted.catalog_for('work'))

    assert restarted.select('work', 'first/model').window == 100_000
    assert restarted.select('work', 'second/model').window == 200_000


def test_catalogue_windows_are_scoped_to_account(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _add(service, 'one')
    _seed_catalog(
        service, ModelCatalog(models=(ModelCapability('first/model', context_window=50_000),), source='remote')
    )
    _add(service, 'two')
    service.catalog.invalidate()

    assert service.select('one', 'first/model').window == 100_000
    assert service.select('two', 'first/model').window == 50_000


def test_new_window_replaces_old_and_failed_refresh_preserves_it(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _add(service)
    observed: list[int] = []
    service.on_window = observed.append
    updated = ModelCatalog(models=(ModelCapability('first/model', context_window=150_000),), source='remote')
    _seed_catalog(service, updated)
    asyncio.run(service.catalog_for('work'))
    assert observed == [150_000]
    assert service.select('work', 'first/model').window == 150_000

    failed = ModelCatalog(models=(ModelCapability('first/model'),), source='static', error='offline')
    _seed_catalog(service, failed)
    asyncio.run(service.catalog_for('work'))
    assert service.select('work', 'first/model').window == 150_000
    assert _service(tmp_path).stored_window() == 150_000
