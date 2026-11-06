"""Two things that followed the wrong owner after a switch.

Both bugs are the same shape: a piece of state was re-pointed in the model and the
widget that paints it kept the old object.

- **The roadmap.** Loading a session brought its plan back, and `/new` kept showing
  it: the registry had swapped the store, and the panel was still reading the one it
  painted last.
- **The worktree badge.** `/new --worktree` moved the process and the title, and the
  status bar kept the label of the tree it came from, because nothing repainted it
  after the switch.

The tests are end to end through the app: the panel's text and the bar's badge, not
the objects behind them.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.test import TestModel

from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import SessionStore
from src.engine.workspace.manager import WorkspaceManager
from src.ui.app import SigilApp
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / 'project'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / 'README.md').write_text('hello\n', encoding='utf-8')
    _git(root, 'add', '.')
    _git(root, 'commit', '-q', '-m', 'init')
    return root


def _app(repo: Path, tmp_path: Path, *, session_messages: tuple[str, ...] = ()) -> SigilApp:
    store = SessionStore(cwd=repo, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    if session_messages:
        session.messages = [ModelRequest(parts=[UserPromptPart(content=text)]) for text in session_messages]
        store.save(session)
    roadmaps = RoadmapStores(session_id=session.id, base_dir=tmp_path / 'sessions')
    manager = WorkspaceManager(
        repo,
        worktrees_dir=tmp_path / 'worktrees',
        session_store=store,
        rebindables=[],
    )
    return SigilApp(
        agent=Agent(TestModel(), instructions='x'),
        store=store,
        session=session,
        roadmap_store=roadmaps,
        workspace_manager=manager,
    )


def _panel(app: SigilApp) -> str:
    return app.query_one(SidebarPanel).text


# --- the roadmap -------------------------------------------------------------


def test_loading_a_saved_session_brings_back_its_own_plan(monkeypatch, tmp_path, repo):
    """`/load` re-reads the plan from disk, not from the store it was holding."""
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        saved = app._store.create_session()
        # Written by a session that is not the one on screen.
        app._roadmap_store.bind_session(saved.id)
        app._roadmap_store.root.set_items([TodoItem(title='Plan of the saved session')])
        app._roadmap_store.bind_session(app._session.id)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert 'Plan of the saved session' not in _panel(app)

            loaded = app._store.load(saved.id)
            assert loaded is not None
            app._session = loaded
            app.bind_roadmap()
            await pilot.pause()
            return _panel(app)

    text = asyncio.run(inner())
    assert 'Plan of the saved session' in text


def test_new_after_loading_a_session_starts_an_empty_plan(monkeypatch, tmp_path, repo):
    """The plan on screen belongs to the conversation on screen, not to the last one."""

    async def inner():
        app = _app(repo, tmp_path)
        # A previous conversation with a plan of its own.
        previous = app._store.create_session()
        app._roadmap_store.bind_session(previous.id)
        app._roadmap_store.root.set_items([TodoItem(title='Old plan from before')])

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert 'Old plan from before' in _panel(app)

            await app.handle_command('/new', '--worktree fresh')
            for _ in range(40):
                await pilot.pause()
                if 'Old plan from before' not in _panel(app):
                    break
            return _panel(app)

    text = asyncio.run(inner())
    assert 'Old plan from before' not in text


def test_the_panel_shows_the_plan_of_the_session_being_viewed(monkeypatch, tmp_path, repo):
    """Loading a session replaces the plan, in both directions."""

    async def inner():
        app = _app(repo, tmp_path)
        first = app._session
        app._roadmap_store.bind_session(first.id)
        app._roadmap_store.root.set_items([TodoItem(title='Plan one')])

        second = app._store.create_session()
        app._roadmap_store.bind_session(second.id)
        app._roadmap_store.root.set_items([TodoItem(title='Plan two')])

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # The app is on the second one; the panel says so.
            assert 'Plan two' in _panel(app)

            app.bind_session(first)
            await pilot.pause()
            return _panel(app)

    text = asyncio.run(inner())
    assert 'Plan one' in text
    assert 'Plan two' not in text


def test_a_rebound_session_does_not_lose_what_was_written_to_it(tmp_path, repo):
    """Re-binding the same session is a no-op, not a reset.

    It happens on every mount and on every view change. Recreating the stores there
    would drop live listeners and the items of a plan that is still open.
    """
    stores = RoadmapStores(session_id='session1', base_dir=tmp_path / 'roadmaps')
    stores.root.set_items([TodoItem(title='Still here')])

    stores.bind_session('session1')

    assert [item.title for item in stores.root.snapshot()] == ['Still here']


# --- the worktree badge ------------------------------------------------------


def test_creating_a_worktree_repaints_the_status_bar(monkeypatch, tmp_path, repo):
    """The bar says which tree the agent is writing to, from the first second."""

    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            bar = app.query_one(StatusBar)
            before = bar.render().plain if callable(bar.render) else str(bar.render())
            assert 'main' in before

            await app.handle_command('/new', '--worktree fix-login')
            await pilot.pause()
            after = bar.render().plain if callable(bar.render) else str(bar.render())
            return before, after, app.workspace_badge()

    before, after, badge = asyncio.run(inner())
    assert 'main' in before
    assert 'fix-login' in after, 'the bar kept the label of the tree it came from'
    assert badge is not None and badge.is_worktree and badge.label == 'fix-login'
