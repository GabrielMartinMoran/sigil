"""End-to-end tests of the provider commands inside the real app.

These cover the seams a unit test cannot: the wire between a screen, the mixin,
the service and the side panel. The catalogue and the credential probe are faked,
so nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from textual.widgets import OptionList

from src.engine.sessions.session_store import SessionStore
from src.ui.widgets.effort_screen import EffortScreen
from src.ui.widgets.sidebar_panel import SidebarPanel
from tests.support.provider_commands import _app, _submit, _texts
from tests.support.provider_setup import _prepared
from tests.support.ui import wait_until


def test_effort_without_a_provider_points_at_the_setup_command():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(Path(tmp))
            async with app.run_test() as pilot:
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
                _submit(app, '/effort')
                await wait_until(pilot, lambda: isinstance(app.screen, EffortScreen))

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
                _submit(app, '/effort')
                await wait_until(pilot, lambda: isinstance(app.screen, EffortScreen))

                await pilot.press('down')  # default -> low
                await pilot.press('enter')
                await wait_until(pilot, lambda: service.snapshot().thinking == 'low')

                assert service.snapshot().thinking == 'low'

    asyncio.run(inner())


def test_effort_sets_the_level_and_the_panel_follows():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            session = SessionStore(cwd=str(root), base_dir=root / 'sessions').create_session()
            app = _app(root, service, session)
            async with app.run_test() as pilot:
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
            async with app.run_test():
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
            async with app.run_test():
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
            async with app.run_test():
                restored = app._restore_session_model(saved)
                del restored
                assert service.snapshot().model == 'second/model'
                assert service.snapshot().thinking == 'medium'

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
            async with app.run_test():
                app._restore_session_model(legacy)
                assert service.snapshot().model == 'first/model'

    asyncio.run(inner())
