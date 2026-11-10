"""Everything handed to `run_worker` must be runnable.

Textual runs the work in a worker and fails it with

    WorkerError: Unsupported attempt to run an async worker

when the work is neither a coroutine, nor awaitable, nor a callable — and then paints
a traceback over the app. The app keeps running, which is what makes this expensive
to notice: `run_worker(self.show_chat())` looks like a call and *is* one, so the
argument is a `bool` and the effect has already happened by the time the panel
appears.

So the invariant is checked where it can be stated exactly: every value that reaches
`run_worker` has to be something the worker can run. A misplaced `await` — or a
missing one — is then a failing test instead of a traceback in a screenshot.

State-based assertions were tried first and are useless here: `WorkerManager` drops a
worker from its set as soon as it finishes, so a failed worker is gone before the test
can look at it.
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.agent_tree_screen import AgentTreeScreen

pytestmark = pytest.mark.usefixtures('memory_databases')


class WorkRecorder:
    """Records what the app hands to `run_worker`, and lets it run as usual."""

    def __init__(self, app: SigilApp) -> None:
        self.handed: list[Any] = []
        self._original = app.run_worker
        app.run_worker = self.__call__  # type: ignore[method-assign]

    def __call__(self, work: Any, **kwargs: Any) -> Any:
        self.handed.append(work)
        return self._original(work, **kwargs)

    @property
    def unrun(self) -> list[Any]:
        """The values Textual cannot run: neither awaitable nor callable."""
        return [work for work in self.handed if not _runnable(work)]


def _runnable(work: Any) -> bool:
    """What `Worker._run_async` accepts."""
    if inspect.iscoroutinefunction(work):
        return True
    if hasattr(work, 'func') and inspect.iscoroutinefunction(work.func):
        return True
    if inspect.isawaitable(work):
        return True
    return callable(work) and not isinstance(work, bool)


def _app(tmp: str) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    registry = AgentRegistry([('builtin', Path(tmp) / 'none')], active=None)
    trees = AgentTreeStore()
    trees.bind(session, root_name='wizard')
    summoner = SubagentSummoner(trees=trees, registry=registry, builder=lambda: lambda profile: None)
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        agent_registry=registry,
        agent_tree=trees,
        summoner=summoner,
    )


def _waiting_node(app: SigilApp) -> Any:
    tree = app._agent_tree.active
    tree.ensure_root('wizard')
    node = tree.add(
        parent_id=tree.root_id,
        name='researcher',
        role=AgentRole.SUBAGENT,
        task='read it',
    )
    node.mode = 'async'
    node.finish('a report')
    node.delivered = False
    return node


def _other_node(app: SigilApp) -> Any:
    tree = app._agent_tree.active
    return tree.add(
        parent_id=tree.root_id,
        name='reviewer',
        role=AgentRole.SUBAGENT,
        task='review',
    )


def test_waking_with_a_subagent_on_screen_hands_only_runnable_work():
    """The case from the screenshot: a report lands while a node is on screen."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            recorder = WorkRecorder(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                _waiting_node(app)
                await app.open_subagent(_other_node(app))
                await pilot.pause()
                assert app.subagent_open is True

                app._wake_from_subagents()
                await pilot.pause()
                return recorder.unrun, app.subagent_open

    unrun, still_open = asyncio.run(inner())
    assert unrun == [], f'`run_worker` was given something it cannot run: {unrun}'
    # And the wake-up did its job: the chat comes back so the turn can run.
    assert still_open is False


def test_a_normal_wake_up_hands_only_runnable_work():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            recorder = WorkRecorder(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                _waiting_node(app)
                app.subagents_idle()
                app._wake_from_subagents()
                await pilot.pause()
                return recorder.unrun

    assert asyncio.run(inner()) == []


def test_the_view_toggles_hand_only_runnable_work():
    """Both ways out of a subagent, and the debug view that shares the space."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            recorder = WorkRecorder(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                node = _other_node(app)

                await app.open_subagent(node)
                await pilot.pause()
                app.action_toggle_debug()
                await pilot.pause()

                await app.open_subagent(node)
                await pilot.pause()
                await pilot.press('escape')

                await app.open_subagent(node)
                await pilot.pause()
                app.show_chat()
                await pilot.pause()
                return recorder.unrun

    assert asyncio.run(inner()) == []


def test_opening_the_tree_hands_only_runnable_work():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            recorder = WorkRecorder(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                tree = app._agent_tree.active
                tree.ensure_root('wizard')
                tree.add(parent_id=tree.root_id, name='researcher', role=AgentRole.SUBAGENT, task='a')
                app.run_worker(app.open_tree())
                for _ in range(30):
                    await pilot.pause()
                    if isinstance(app.screen, AgentTreeScreen):
                        break
                assert isinstance(app.screen, AgentTreeScreen)
                return recorder.unrun

    assert asyncio.run(inner()) == []


def test_the_recorder_recognises_the_bug():
    """The check itself: a `bool` is what the traceback was about."""
    pending = asyncio.sleep(0)
    try:
        assert _runnable(pending) is True
    finally:
        pending.close()
    assert _runnable(lambda: None) is True
    assert _runnable(True) is False
    assert _runnable(False) is False
    assert _runnable('a string') is False
