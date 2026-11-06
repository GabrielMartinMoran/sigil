"""Integration test of the roadmap section of the side panel."""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.sidebar_panel import SidebarPanel


def test_roadmap_panel_shows_and_updates(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=session.id)
            roadmap.root.set_items([TodoItem(title='Task A', status='in_progress')])

            app = SigilApp(
                agent=Agent(TestModel(), instructions='x'),
                store=store,
                session=session,
                roadmap_store=roadmap,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)
                assert panel.display is True
                assert 'Task A' in panel.text

                roadmap.root.set_items([TodoItem(title='Task B', status='done')])
                await pilot.pause()
                assert 'Task B' in panel.text

    asyncio.run(inner())


def test_new_session_gets_its_own_empty_roadmap(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=session.id)
            roadmap.root.set_items([TodoItem(title='Old task', status='done')])

            app = SigilApp(
                agent=Agent(TestModel(), instructions='x'),
                store=store,
                session=session,
                roadmap_store=roadmap,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)
                assert 'Old task' in panel.text

                await app.handle_command('/new')
                await pilot.pause()

                assert roadmap.root.session_id == app._session.id
                assert roadmap.root.snapshot() == []
                assert 'Old task' not in panel.text

    asyncio.run(inner())


def test_loading_a_session_restores_its_roadmap(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            first = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=first.id)
            roadmap.root.set_items([TodoItem(title='Task from the first', status='in_progress')])

            second = store.create_session()
            roadmap.bind_session(second.id)
            roadmap.root.set_items([TodoItem(title='Task from the second', status='pending')])

            app = SigilApp(
                agent=Agent(TestModel(), instructions='x'),
                store=store,
                session=second,
                roadmap_store=roadmap,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)
                assert 'Task from the second' in panel.text

                app._session = store.load(first.id)
                app.bind_roadmap()
                await pilot.pause()

                assert 'Task from the first' in panel.text
                assert 'Task from the second' not in panel.text

    asyncio.run(inner())


def _chat_texts(app) -> list[str]:
    """Plain text of every chat message."""
    texts = []
    for child in app.query_one(ChatLog).children:
        try:
            rendered = child.render()
        except Exception:  # noqa: BLE001, S112 - widgets without their own render
            continue
        plain = getattr(rendered, 'plain', None)
        if isinstance(plain, str):
            texts.append(plain)
    return texts


def _sidebar_app(tmp: str, roadmap: RoadmapStores, session) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(), instructions='x'),
        store=store,
        session=session,
        roadmap_store=roadmap,
    )


def test_sidebar_command_hides_and_shows_the_panel(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=session.id)
            roadmap.root.set_items([TodoItem(title='Task A')])

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)
                assert panel.display is True

                await app.handle_command('/sidebar', 'off')
                await pilot.pause()
                assert panel.display is False
                assert panel.shown is False

                await app.handle_command('/sidebar', 'on')
                await pilot.pause()
                assert panel.display is True
                assert panel.shown is True

    asyncio.run(inner())


def test_sidebar_command_without_argument_toggles(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)

                await app.handle_command('/sidebar')
                await pilot.pause()
                assert panel.display is False

                await app.handle_command('/sidebar')
                await pilot.pause()
                assert panel.display is True

    asyncio.run(inner())


def test_hidden_sidebar_stays_hidden_when_the_roadmap_changes(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)

                await app.handle_command('/sidebar', 'off')
                await pilot.pause()

                roadmap.root.set_items([TodoItem(title='New task', status='in_progress')])
                await pilot.pause()

                assert panel.display is False

    asyncio.run(inner())


def test_sidebar_command_rejects_unknown_arguments(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)

                await app.handle_command('/sidebar', 'maybe')
                await pilot.pause()

                assert panel.display is True
                assert 'Usage: /sidebar' in ' '.join(_chat_texts(app))

    asyncio.run(inner())


def test_sidebar_command_typed_in_the_prompt(monkeypatch):
    """The argument arrives from the prompt, not only from a direct call."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStores(directory=Path(tmp), session_id=session.id)

            app = _sidebar_app(tmp, roadmap, session)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)
                assert panel.display is True

                app.query_one(PromptInput).value = '/sidebar off'
                await pilot.press('enter')
                await pilot.pause()

                assert panel.display is False

    asyncio.run(inner())
