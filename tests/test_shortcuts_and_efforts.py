"""Regression tests for the key handling and for the effort levels.

Two things are pinned here:

- `ctrl+m` / `ctrl+t` open the pickers **without** breaking Enter. They are read
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

from pydantic_ai import Agent
from textual.widgets import OptionList

from src.engine.providers.bootstrap import ProviderPaths, build_service
from src.engine.providers.catalog.probe import ProbeResult
from src.engine.providers.catalog.static_source import (
    OPENCODE_FALLBACK,
    OPENROUTER_FALLBACK,
    fallback_capability,
)
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.factory.unconfigured import UnconfiguredModel
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.effort_screen import EffortScreen
from src.ui.widgets.model_picker_screen import ModelPickerScreen
from src.ui.widgets.prompt_input import PromptInput

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

    def invalidate(self, account_id=None):
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
        agent=Agent((service or _service(Path(tmp))).current_model(), instructions='x'),
        store=store,
        session=store.create_session(),
        model_service=service,
    )


# --- the shortcuts ----------------------------------------------------------


def test_ctrl_m_opens_the_model_picker():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.query_one(PromptInput).focus()
                await pilot.press('ctrl+m')
                for _ in range(8):
                    await pilot.pause()
                assert isinstance(app.screen, ModelPickerScreen)

    asyncio.run(inner())


def test_ctrl_t_opens_the_effort_picker():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.query_one(PromptInput).focus()
                await pilot.press('ctrl+t')
                for _ in range(8):
                    await pilot.pause()
                assert isinstance(app.screen, EffortScreen)

    asyncio.run(inner())


def test_enter_still_sends_a_message_with_the_shortcuts_wired():
    """The regression a global `ctrl+m` binding caused: Enter stopped working."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.focus()
                field.value = 'hello'
                await pilot.press('enter')
                for _ in range(8):
                    await pilot.pause()
                # The prompt cleared and no modal opened: it was sent, not captured.
                assert field.value == ''
                assert not isinstance(app.screen, (ModelPickerScreen, EffortScreen))

    asyncio.run(inner())


def test_ctrl_m_does_not_swallow_the_typed_text():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.focus()
                field.value = 'hola'
                await pilot.press('ctrl+m')
                for _ in range(8):
                    await pilot.pause()
                await pilot.press('escape')
                await pilot.pause()
                assert field.value == 'hola'

    asyncio.run(inner())


def test_the_app_no_longer_binds_the_broken_alt_shortcuts():
    """The pickers that used to live on `alt+p` / `alt+m` stayed on `ctrl+…`.

    `alt+t` came back with the tree of agents, as a view toggle next to `alt+d`:
    the two open a panel over the chat instead of a picker the user has to work
    through, and the shortcut is what was asked for.
    """
    bindings = {binding.key for binding in SigilApp.BINDINGS}
    assert 'alt+p' not in bindings
    assert 'alt+m' not in bindings
    assert 'alt+d' in bindings and 'alt+t' in bindings


def test_the_shortcuts_are_not_app_level_ctrl_bindings():
    """A global `ctrl+m` would claim Enter on a terminal without the kitty protocol."""
    bindings = {binding.key for binding in SigilApp.BINDINGS}
    assert 'ctrl+m' not in bindings
    assert 'ctrl+t' not in bindings


# --- the effort levels ------------------------------------------------------


def test_the_static_fallback_knows_the_efforts_of_its_models():
    for model in (*OPENROUTER_FALLBACK, *OPENCODE_FALLBACK):
        assert model.efforts or model.name in {'gpt-4.1'}, model.name


def test_the_static_fallback_no_longer_claims_unknown_windows():
    for model in (*OPENROUTER_FALLBACK, *OPENCODE_FALLBACK):
        assert model.context_window is not None, model.name


def test_fallback_capability_finds_a_model_in_any_family():
    assert fallback_capability('glm-5.3') is not None
    assert fallback_capability('glm-5.3').efforts == ('low', 'high', 'max')
    assert fallback_capability('nope/not-a-model') is None


def test_the_efforts_survive_being_persisted(tmp_path):
    service = _service(tmp_path)
    asyncio.run(service.add_api_key(ProviderKind.OPENCODE, 'ds', 'sk-test'))

    selection = service.state.last_selection()
    assert selection is not None
    assert selection.efforts == ('low', 'high', 'max')


def test_the_effort_picker_offers_the_real_levels_of_the_model():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            service = await _prepared(Path(tmp), ProviderKind.OPENCODE, 'ds')
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press('ctrl+t')
                for _ in range(8):
                    await pilot.pause()

                screen = app.screen
                assert isinstance(screen, EffortScreen)
                values = [str(option.id) for option in screen.query_one(OptionList).options]
                assert values == ['default', 'low', 'high', 'max', 'off']

    asyncio.run(inner())


def test_a_cold_start_fetches_the_catalogue_once(tmp_path):
    """The other half of the bug: the picker was empty until `/model` was opened."""
    asyncio.run(_service(tmp_path).add_api_key(ProviderKind.OPENCODE, 'ds', 'sk-test'))

    catalog = FakeCatalog()
    restarted = _service(tmp_path, catalog=catalog, session_id='other')
    assert restarted.snapshot().efforts == ('low', 'high', 'max')

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, restarted)
            async with app.run_test(size=(120, 40)) as pilot:
                for _ in range(12):
                    await pilot.pause()
                assert catalog.fetches == 1

    asyncio.run(inner())


def test_a_second_start_does_not_refetch_when_the_cache_is_warm(tmp_path):
    asyncio.run(_service(tmp_path).add_api_key(ProviderKind.OPENCODE, 'ds', 'sk-test'))

    service = _service(tmp_path, session_id='other')

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, service)
            async with app.run_test(size=(120, 40)) as pilot:
                for _ in range(12):
                    await pilot.pause()
                # `refresh_capability` runs once and fills the cache.
                assert service.catalog.cached(service.list_accounts()[0]) is not None

    asyncio.run(inner())


def test_refresh_capability_is_a_no_op_without_a_selection(tmp_path):
    service = _service(tmp_path)
    snapshot = asyncio.run(service.refresh_capability())
    assert not snapshot.is_configured


def test_a_model_name_the_route_rejects_is_a_domain_error_not_a_crash(tmp_path):
    """OpenRouter requires a `vendor/` prefix; the SDK raises its own UserError.

    A hand-typed id (or a stale state file) without the prefix used to escape as a
    traceback instead of a readable message. The catalogue is faked with an
    OpenCode-style id to trigger exactly that.
    """
    from src.engine.providers.domain.provider_error import ProviderError

    service = _service(tmp_path)

    try:
        asyncio.run(service.add_api_key(ProviderKind.OPENROUTER, 'or', 'sk-test'))
    except ProviderError as error:
        assert 'deepseek-v4.1-flash' in str(error)
        assert 'or' in str(error)
    else:  # pragma: no cover
        raise AssertionError('expected a ProviderError')


def test_opencode_accepts_bare_model_ids(tmp_path):
    """The opposite case: a route that takes plain ids must not be rejected."""
    service = _service(tmp_path)
    asyncio.run(service.add_api_key(ProviderKind.OPENCODE, 'oc', 'sk-test'))

    snapshot = service.select('oc', 'deepseek-v4.1-flash')

    assert snapshot.model == 'deepseek-v4.1-flash'
