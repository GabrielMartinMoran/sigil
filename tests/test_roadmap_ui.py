"""Test de integración del panel de roadmap en la app."""

import asyncio
import os
import tempfile
from pathlib import Path

os.environ.setdefault('LLM_MODEL', 'test/model')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.app as appmod
from src.engine.roadmap.store import RoadmapStore, TodoItem
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.roadmap_panel import RoadmapPanel


async def _fake_fetch(model_id):
    return None


def test_roadmap_panel_shows_and_updates(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=session.id)
            roadmap.set_items([TodoItem(title='Tarea A', status='in_progress')])

            app = SigilApp(
                agent=Agent(TestModel(), instructions='x'),
                store=store,
                session=session,
                roadmap_store=roadmap,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)
                assert panel.display is True
                assert 'Tarea A' in panel.render().plain

                roadmap.set_items([TodoItem(title='Tarea B', status='done')])
                await pilot.pause()
                assert 'Tarea B' in panel.render().plain

    asyncio.run(inner())


def test_new_session_gets_its_own_empty_roadmap(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=session.id)
            roadmap.set_items([TodoItem(title='Tarea vieja', status='done')])

            app = SigilApp(
                agent=Agent(TestModel(), instructions='x'),
                store=store,
                session=session,
                roadmap_store=roadmap,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)
                assert 'Tarea vieja' in panel.render().plain

                await app._handle_command('/new')
                await pilot.pause()

                assert roadmap.session_id == app._session.id
                assert roadmap.snapshot() == []
                assert 'Tarea vieja' not in panel.render().plain

    asyncio.run(inner())


def test_loading_a_session_restores_its_roadmap(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            first = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=first.id)
            roadmap.set_items([TodoItem(title='Tarea de la primera', status='in_progress')])

            second = store.create_session()
            roadmap.bind_session(second.id)
            roadmap.set_items([TodoItem(title='Tarea de la segunda', status='pending')])

            app = SigilApp(
                agent=Agent(TestModel(), instructions='x'),
                store=store,
                session=second,
                roadmap_store=roadmap,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)
                assert 'Tarea de la segunda' in panel.render().plain

                app._session = store.load(first.id)
                app._bind_roadmap()
                await pilot.pause()

                assert 'Tarea de la primera' in panel.render().plain
                assert 'Tarea de la segunda' not in panel.render().plain

    asyncio.run(inner())


def _chat_texts(app) -> list[str]:
    """Texto plano de cada mensaje del chat."""
    texts = []
    for child in app.query_one(ChatLog).children:
        try:
            rendered = child.render()
        except Exception:  # noqa: BLE001, S112 - widgets sin render propio
            continue
        plain = getattr(rendered, 'plain', None)
        if isinstance(plain, str):
            texts.append(plain)
    return texts


def _sidebar_app(tmp: str, roadmap: RoadmapStore, session) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(), instructions='x'),
        store=store,
        session=session,
        roadmap_store=roadmap,
    )


def test_sidebar_command_hides_and_shows_the_panel(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=session.id)
            roadmap.set_items([TodoItem(title='Tarea A')])

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)
                assert panel.display is True

                await app._handle_command('/sidebar', 'off')
                await pilot.pause()
                assert panel.display is False
                assert panel.shown is False

                await app._handle_command('/sidebar', 'on')
                await pilot.pause()
                assert panel.display is True
                assert panel.shown is True

    asyncio.run(inner())


def test_sidebar_command_without_argument_toggles(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)

                await app._handle_command('/sidebar')
                await pilot.pause()
                assert panel.display is False

                await app._handle_command('/sidebar')
                await pilot.pause()
                assert panel.display is True

    asyncio.run(inner())


def test_hidden_sidebar_stays_hidden_when_the_roadmap_changes(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)

                await app._handle_command('/sidebar', 'off')
                await pilot.pause()

                roadmap.set_items([TodoItem(title='Tarea nueva', status='in_progress')])
                await pilot.pause()

                assert panel.display is False

    asyncio.run(inner())


def test_sidebar_command_rejects_unknown_arguments(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)

                await app._handle_command('/sidebar', 'quizás')
                await pilot.pause()

                assert panel.display is True
                assert 'Uso: /sidebar' in ' '.join(_chat_texts(app))

    asyncio.run(inner())


def test_sidebar_command_typed_in_the_prompt(monkeypatch):
    """El argumento llega desde el prompt, no sólo desde una llamada directa."""
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)
                assert panel.display is True

                app.query_one(PromptInput).value = '/sidebar off'
                await pilot.press('enter')
                await pilot.pause()

                assert panel.display is False

    asyncio.run(inner())
