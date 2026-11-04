"""End-to-end tests of the provider commands inside the real app.

These cover the seams a unit test cannot: the wire between a screen, the mixin,
the service and the side panel. The catalogue and the credential probe are faked,
so nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets import Input, OptionList

from src.engine.providers.bootstrap import ProviderPaths, build_service
from src.engine.providers.catalog.probe import ProbeResult
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.factory.unconfigured import UnconfiguredModel
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.effort_screen import EffortScreen
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.sidebar_panel import SidebarPanel

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

    def invalidate(self, account_id=None):
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


def _app(tmp_path: Path, service=None, session=None) -> SigilApp:
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    session = session or store.create_session()
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        model_service=service or _service(tmp_path),
    )


def _try_render(child):
    """The child's renderable, or ``None`` when it has none."""
    try:
        return child.render()
    except Exception:  # noqa: BLE001 - optional protocol on the widget
        return None


def _texts(app) -> list[str]:
    """Plain text of every chat message, rendered as the widget does."""
    out = []
    for child in app.query_one(ChatLog).children:
        # Some chat children are containers with no renderable of their own.
        rendered = _try_render(child)
        plain = getattr(rendered, 'plain', None)
        if isinstance(plain, str):
            out.append(plain)
    return out


def _submit(app, text: str) -> None:
    """Types a command and submits it, bypassing the command menu.

    `pilot.press` on a string with a space opens the menu and `Enter` then picks
    the highlighted command, which is not what these tests are about.
    """
    field = app.query_one(PromptInput)
    field.value = text
    field.post_message(Input.Submitted(field, text))


async def _wait_idle(app, pilot, attempts: int = 40) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if not app.query_one(PromptInput).disabled:
            return


def test_effort_without_a_provider_points_at_the_setup_command():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(Path(tmp))
            async with app.run_test() as pilot:
                await pilot.pause()
                _submit(app, '/effort')
                await pilot.pause()
                assert any('/provider' in text for text in _texts(app))

    asyncio.run(inner())


def test_effort_without_argument_opens_the_picker_with_the_accepted_levels():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            session = SessionStore(cwd=str(root), base_dir=root / 'sessions').create_session()
            app = _app(root, service, session)
            async with app.run_test() as pilot:
                await pilot.pause()
                _submit(app, '/effort')
                for _ in range(8):
                    await pilot.pause()

                screen = app.screen
                assert isinstance(screen, EffortScreen)
                values = [str(option.id) for option in screen.query_one(OptionList).options]
                assert values == ['default', 'low', 'high', 'off']

    asyncio.run(inner())


def test_the_effort_picker_applies_the_highlighted_level():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            session = SessionStore(cwd=str(root), base_dir=root / 'sessions').create_session()
            app = _app(root, service, session)
            async with app.run_test() as pilot:
                await pilot.pause()
                _submit(app, '/effort')
                for _ in range(8):
                    await pilot.pause()

                await pilot.press('down')  # default -> low
                await pilot.press('enter')
                for _ in range(8):
                    await pilot.pause()

                assert service.snapshot().thinking == 'low'

    asyncio.run(inner())


def _kind():
    from src.engine.providers.domain.provider_kind import ProviderKind

    return ProviderKind.OPENROUTER


def test_effort_sets_the_level_and_the_panel_follows():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            session = SessionStore(cwd=str(root), base_dir=root / 'sessions').create_session()
            app = _app(root, service, session)
            async with app.run_test() as pilot:
                await pilot.pause()
                _submit(app, '/effort high')
                await pilot.pause()
                assert service.snapshot().thinking == 'high'
                assert 'effort: high' in app.query_one(SidebarPanel).text

    asyncio.run(inner())


def test_effort_rejects_a_level_the_model_does_not_accept():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            session = SessionStore(cwd=str(root), base_dir=root / 'sessions').create_session()
            app = _app(root, service, session)
            async with app.run_test() as pilot:
                await pilot.pause()
                _submit(app, '/effort max')
                await pilot.pause()
                assert service.snapshot().thinking == 'default'
                assert any('does not accept' in text for text in _texts(app))

    asyncio.run(inner())


def test_the_panel_shows_the_active_account_and_its_context():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            session = SessionStore(cwd=str(root), base_dir=root / 'sessions').create_session()
            app = _app(root, service, session)
            async with app.run_test() as pilot:
                await pilot.pause()
                sidebar = app.query_one(SidebarPanel).text
                assert 'work' in sidebar
                assert 'OpenRouter' in sidebar
                assert 'first/model' in sidebar
                # The window comes from the catalogue entry; a fresh session
                # projects zero, so the pair is there from the first paint.
                assert '0% (0 / 100K)' in sidebar

    asyncio.run(inner())


def test_the_panel_says_no_model_before_anything_is_configured():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(Path(tmp))
            async with app.run_test() as pilot:
                await pilot.pause()
                sidebar = app.query_one(SidebarPanel).text
                assert '/provider' in sidebar
                # A context percentage for a window that does not exist yet is a
                # number that means nothing.
                assert 'Context' not in sidebar

    asyncio.run(inner())


def test_loading_a_session_restores_its_model():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            store = SessionStore(cwd=str(root), base_dir=root / 'sessions')
            service.select('work', 'second/model', 'medium')

            saved = store.create_session()
            saved.selection = service.snapshot().selection
            store.save(saved)
            service.select('work', 'first/model', 'default')
            assert service.snapshot().model == 'first/model'

            app = _app(root, service, saved)
            async with app.run_test() as pilot:
                await pilot.pause()
                restored = app._restore_session_model(saved)
                del restored
                assert service.snapshot().model == 'second/model'
                assert service.snapshot().thinking == 'medium'

    asyncio.run(inner())


def test_the_selection_survives_being_written_and_read_by_the_store():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            store = SessionStore(cwd=str(root), base_dir=root / 'sessions')
            service.select('work', 'second/model', 'high')

            session = store.create_session()
            session.selection = service.snapshot().selection
            store.save(session)

            reloaded = store.load(session.id)
            assert reloaded is not None
            assert reloaded.selection is not None
            assert reloaded.selection.account_id == service.list_accounts()[0].id
            assert reloaded.selection.model == 'second/model'
            assert reloaded.selection.thinking == 'high'
            # The window and the effort levels travel with the selection so a cold
            # start shows the real context size instead of `?`.
            assert reloaded.selection.window == 200_000
            assert reloaded.selection.efforts == ('low', 'medium', 'high')

    asyncio.run(inner())


def test_a_session_without_a_selection_loads_without_touching_the_model():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            store = SessionStore(cwd=str(root), base_dir=root / 'sessions')
            legacy = store.create_session()
            assert legacy.selection is None

            app = _app(root, service, legacy)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._restore_session_model(legacy)
                assert service.snapshot().model == 'first/model'

    asyncio.run(inner())
