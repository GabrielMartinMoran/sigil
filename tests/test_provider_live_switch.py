"""Regression tests for the two bugs that needed a restart to notice.

- Switching provider or model did nothing until Sigil was restarted, because the
  agent was built with a concrete model instead of the router.
- The context window was unknown on every start, because it only lived in the
  catalogue's in-memory cache.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import Agent

from src.engine.providers.bootstrap import ProviderPaths, build_service
from src.engine.providers.catalog.probe import ProbeResult
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.selection import Selection
from src.engine.providers.factory.unconfigured import UnconfiguredModel

CATALOG = ModelCatalog(
    models=(
        ModelCapability(name='first/model', context_window=100_000, efforts=('low', 'high')),
        ModelCapability(name='second/model', context_window=200_000, efforts=('low', 'medium', 'high')),
    ),
    source='remote',
)


async def _accept(_account, _key):
    return ProbeResult('valid')


class FakeCatalog:
    """The catalogue with a per-process cache, like the real one."""

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

    def invalidate(self, account_id=None):
        if account_id is None:
            self._cache.clear()
        else:
            self._cache.pop(account_id, None)


def _service(tmp_path: Path, session_id: str = 's', catalog=None):
    return build_service(
        ProviderPaths(tmp_path / 'providers'),
        session_id=session_id,
        initial_model=UnconfiguredModel(),
        probe=_accept,
        catalog=catalog or FakeCatalog(),
    )


def _add(service, alias: str = 'work'):
    return asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, alias, 'sk-test'))


# --- the agent must hold the router ----------------------------------------


def test_current_model_returns_the_router_not_the_wrapped_model(tmp_path):
    service = _service(tmp_path)
    _add(service)

    assert service.current_model() is service.router
    assert service.current_model() is not service.router.current


def test_a_switch_reaches_an_agent_built_before_it(tmp_path):
    """The original symptom: the change only applied after a restart."""
    service = _service(tmp_path)
    agent = Agent(service.current_model(), instructions='x')

    _add(service)
    assert agent.model.model_name == 'first/model'

    service.select('work', 'second/model', 'high')

    # Same agent object, same router object, new target.
    assert agent.model is service.router
    assert agent.model.model_name == 'second/model'
    assert agent.model.context_window == 200_000


def test_the_agent_sees_the_new_window_after_a_switch(tmp_path):
    service = _service(tmp_path)
    agent = Agent(service.current_model(), instructions='x')
    _add(service)
    before = agent.model.context_window

    service.select('work', 'second/model')

    assert before == 100_000
    assert agent.model.context_window == 200_000


def test_switching_accounts_reaches_the_agent(tmp_path):
    service = _service(tmp_path)
    agent = Agent(service.current_model(), instructions='x')
    _add(service, 'one')
    _add(service, 'two')

    service.select('one', 'first/model')

    assert agent.model.model_name == 'first/model'
    assert service.snapshot().alias == 'one'


# --- the window must survive a restart -------------------------------------


def test_the_selection_stores_the_window_and_the_efforts(tmp_path):
    service = _service(tmp_path)
    _add(service)
    selection = service.state.last_selection()

    assert selection is not None
    assert selection.window == 100_000
    assert selection.efforts == ('low', 'high')


def test_stored_window_reads_the_value_without_the_network(tmp_path):
    _add(_service(tmp_path))
    catalog = FakeCatalog()
    restarted = _service(tmp_path, session_id='other', catalog=catalog)

    assert restarted.catalog.cached(restarted.list_accounts()[0]) is None
    assert restarted.stored_window() == 100_000
    assert catalog.fetches == 0


def test_the_snapshot_after_a_restart_knows_the_window_and_the_efforts(tmp_path):
    _add(_service(tmp_path))
    restarted = _service(tmp_path, session_id='other')

    snapshot = restarted.snapshot()

    assert snapshot.window == 100_000
    assert snapshot.efforts == ('low', 'high')
    assert snapshot.label == 'work:first/model'


def test_the_snapshot_without_a_stored_window_falls_back_to_unknown(tmp_path):
    service = _service(tmp_path)
    _add(service)
    # Simulate a state file written before the window travelled with it, and read
    # it back with a cold catalogue cache: nothing knows the window.
    legacy = Selection(account_id=service.list_accounts()[0].id, model='first/model')
    service.state.set_last_selection(legacy)
    restarted = _service(tmp_path, session_id='other')

    snapshot = restarted.snapshot()

    assert snapshot.window is None
    assert snapshot.efforts == ()


def test_the_snapshot_prefers_the_fresh_catalogue_over_the_stored_value(tmp_path):
    service = _service(tmp_path)
    _add(service)
    service.state.set_last_selection(Selection(account_id=service.list_accounts()[0].id, model='first/model', window=1))
    asyncio.run(service.catalog_for())

    assert service.snapshot().window == 100_000


def test_effort_setting_persists_the_window_alongside_it(tmp_path):
    service = _service(tmp_path)
    _add(service)
    service.set_thinking('high')
    restarted = _service(tmp_path, session_id='other')

    snapshot = restarted.snapshot()

    assert snapshot.thinking == 'high'
    assert snapshot.window == 100_000


def test_a_selection_round_trips_through_json_with_the_capability(tmp_path):
    selection = Selection(
        account_id='abc',
        model='m',
        thinking='high',
        window=200_000,
        efforts=('low', 'high'),
    )
    assert Selection.from_dict(selection.to_dict()) == selection
