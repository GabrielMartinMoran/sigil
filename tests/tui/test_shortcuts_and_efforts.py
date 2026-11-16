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
import tempfile
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets import OptionList

from src.core.providers.bootstrap import build_service
from src.core.providers.catalog.probe import ProbeResult
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.model_catalog import ModelCatalog
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.factory.unconfigured_model import UnconfiguredModel
from src.core.providers.provider_paths import ProviderPaths
from src.core.sessions.session_store import SessionStore
from src.ui.sigil_app import SigilApp
from src.ui.widgets.panels.prompt_input import PromptInput
from src.ui.widgets.screens.effort_screen import EffortScreen
from src.ui.widgets.screens.grouped_model_screen import GroupedModelScreen
from src.ui.widgets.screens.model_picker_screen import ModelPickerScreen
from tests.support.ui import wait_until

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


async def _prepared(tmp_path: Path, kind=ProviderKind.OPENROUTER, alias: str = 'work'):
    """A service with one account configured, outside any running loop."""
    service = _service(tmp_path)
    await service.add_api_key(kind, alias, 'sk-test')
    return service


def _app(tmp: str, service=None) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        model_service=service,
    )


# --- the shortcuts ----------------------------------------------------------


def test_ctrl_m_opens_the_model_picker() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one(PromptInput).focus()
                await pilot.press('ctrl+m')
                await wait_until(pilot, lambda: isinstance(app.screen, GroupedModelScreen))
                assert isinstance(app.screen, GroupedModelScreen)

    asyncio.run(inner())


def test_ctrl_l_opens_the_effort_picker() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one(PromptInput).focus()
                await pilot.press('ctrl+l')
                await wait_until(pilot, lambda: isinstance(app.screen, EffortScreen))
                assert isinstance(app.screen, EffortScreen)

    asyncio.run(inner())


def test_enter_still_sends_a_message_with_the_shortcuts_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression a global `ctrl+m` binding caused: Enter stopped working."""
    import pydantic_ai.models

    monkeypatch.setattr(pydantic_ai.models, 'ALLOW_MODEL_REQUESTS', False)

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                field = app.query_one(PromptInput)
                field.focus()
                field.value = 'hello'
                await pilot.press('enter')
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                # The prompt cleared and the turn completed without opening a modal.
                assert field.value == ''
                assert not isinstance(app.screen, (ModelPickerScreen, EffortScreen))
                assert not app._turn_active
                assert len(app._session.messages) == 2
                assert app._session.messages[-1].parts[0].content == 'ok'

    asyncio.run(inner())


def test_ctrl_m_does_not_swallow_the_typed_text() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                field = app.query_one(PromptInput)
                field.focus()
                field.value = 'hola'
                await pilot.press('ctrl+m')
                await wait_until(pilot, lambda: isinstance(app.screen, GroupedModelScreen))
                await pilot.press('escape')
                assert field.value == 'hola'

    asyncio.run(inner())


def test_the_app_no_longer_binds_the_broken_alt_shortcuts() -> None:
    """The pickers that used to live on `alt+p` / `alt+m` stayed on `ctrl+…`.

    `ctrl+t` belongs to the tree of agents now, as a view toggle next to `alt+d`:
    the two open a panel over the chat instead of a picker the user has to work
    through, and the effort picker moved to `ctrl+l`.
    """
    bindings = {binding.key for binding in SigilApp.BINDINGS}
    assert 'alt+p' not in bindings
    assert 'alt+m' not in bindings
    assert 'alt+t' not in bindings
    assert 'alt+d' in bindings and 'ctrl+t' in bindings


def test_the_shortcuts_are_not_app_level_ctrl_bindings() -> None:
    """A global `ctrl+m` would claim Enter on a terminal without the kitty protocol."""
    bindings = {binding.key for binding in SigilApp.BINDINGS}
    assert 'ctrl+m' not in bindings
    assert 'ctrl+l' not in bindings


def test_the_effort_picker_offers_the_real_levels_of_the_model() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press('ctrl+l')
                await wait_until(pilot, lambda: isinstance(app.screen, EffortScreen))

                screen = app.screen
                assert isinstance(screen, EffortScreen)
                values = [str(option.id) for option in screen.query_one(OptionList).options]
                assert values == ['default', 'low', 'high', 'max', 'off']

    asyncio.run(inner())


def test_a_cold_start_fetches_the_catalogue_once(tmp_path: Path) -> None:
    """The other half of the bug: the picker was empty until `/model` was opened."""
    asyncio.run(_service(tmp_path).add_api_key(ProviderKind.OPENCODE, 'ds', 'sk-test'))

    catalog = FakeCatalog()
    restarted = _service(tmp_path, catalog=catalog, session_id='other')
    assert restarted.snapshot().efforts == ('low', 'high', 'max')

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, restarted)
            async with app.run_test(size=(120, 40)):
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                assert catalog.fetches == 1

    asyncio.run(inner())


def test_a_second_start_does_not_refetch_when_the_cache_is_warm(tmp_path: Path) -> None:
    asyncio.run(_service(tmp_path).add_api_key(ProviderKind.OPENCODE, 'ds', 'sk-test'))

    service = _service(tmp_path, session_id='other')

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)):
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                # `refresh_capability` runs once and fills the cache.
                assert service.catalog.cached(service.list_accounts()[0]) is not None

    asyncio.run(inner())
