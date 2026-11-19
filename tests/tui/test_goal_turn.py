"""A goal continues through the terminal turn path without user input."""

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.core.agents.profile.agent_registry import AgentRegistry
from src.core.agents.tree.agent_tree_store import AgentTreeStore
from src.core.goal.review import GoalDecision
from src.core.goal.state import Goal
from src.core.sessions.session_store import SessionStore
from src.ui.sigil_app import SigilApp


def test_local_goal_continues_until_review_completes(tmp_path: Path, monkeypatch) -> None:
    from src.ui.mixins import turn_mixin as module

    async def run() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        session = store.create_session()
        session.goal = Goal('ship export')
        trees = AgentTreeStore()
        trees.bind(session, root_name='wizard')
        app = SigilApp(
            agent=Agent(TestModel(custom_output_text='work report', call_tools=[]), instructions='x'),
            store=store,
            session=session,
            agent_registry=AgentRegistry([('builtin', tmp_path / 'none')], active=None),
            agent_tree=trees,
        )
        finished = asyncio.Event()
        decisions = iter(
            [
                GoalDecision(decision='continue', reason='Run tests.'),
                GoalDecision(decision='complete', reason='Completed.'),
            ]
        )

        async def review(model, snapshot, **kwargs):
            return next(decisions)

        save = store.save_progress

        def save_progress(value, **kwargs):
            result = save(value, **kwargs)
            if value.goal is not None and value.goal.status == 'completed':
                finished.set()
            return result

        monkeypatch.setattr(store, 'save_progress', save_progress)
        monkeypatch.setattr(module, 'review_goal', review)
        async with app.run_test(size=(120, 40)):
            app.start_turn('Start work')
            await asyncio.wait_for(finished.wait(), 10)
            assert session.goal.status == 'completed'
            assert len(session.messages) >= 4

    asyncio.run(run())


def test_local_abort_cancels_goal_review(tmp_path: Path, monkeypatch) -> None:
    from src.ui.mixins import turn_mixin as module

    async def run() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        session = store.create_session()
        session.goal = Goal('ship export')
        trees = AgentTreeStore()
        trees.bind(session, root_name='wizard')
        app = SigilApp(
            agent=Agent(TestModel(custom_output_text='work report', call_tools=[]), instructions='x'),
            store=store,
            session=session,
            agent_registry=AgentRegistry([('builtin', tmp_path / 'none')], active=None),
            agent_tree=trees,
        )
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def review(model, snapshot, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(module, 'review_goal', review)
        async with app.run_test(size=(120, 40)):
            app.start_turn('Start work')
            await asyncio.wait_for(started.wait(), 10)
            app.abort_turn()
            await asyncio.wait_for(cancelled.wait(), 10)
            assert session.goal.status == 'stopped'

    asyncio.run(run())


def test_local_goal_change_during_review_discards_old_decision(tmp_path: Path, monkeypatch) -> None:
    from src.ui.mixins import turn_mixin as module

    async def run() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        session = store.create_session()
        session.goal = Goal('old')
        trees = AgentTreeStore()
        trees.bind(session, root_name='wizard')
        app = SigilApp(
            agent=Agent(TestModel(custom_output_text='work report', call_tools=[]), instructions='x'),
            store=store,
            session=session,
            agent_registry=AgentRegistry([('builtin', tmp_path / 'none')], active=None),
            agent_tree=trees,
        )
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()
        goals = []
        save = store.save_progress

        async def review(model, snapshot, **kwargs):
            goals.append(snapshot.goal.text)
            if len(goals) == 1:
                started.set()
                await release.wait()
            return GoalDecision(decision='complete', reason='Done')

        def save_progress(value, **kwargs):
            result = save(value, **kwargs)
            if value.goal is not None and value.goal.status == 'completed':
                completed.set()
            return result

        monkeypatch.setattr(store, 'save_progress', save_progress)
        monkeypatch.setattr(module, 'review_goal', review)
        async with app.run_test(size=(120, 40)):
            app.start_turn('Start')
            await asyncio.wait_for(started.wait(), 10)
            session.goal = session.goal.replace('new')
            release.set()
            await asyncio.wait_for(completed.wait(), 10)
            assert goals == ['old', 'new']
            assert session.goal.text == 'new'

    asyncio.run(run())


def test_local_goal_command_steers_active_run_as_internal_notice(tmp_path: Path, monkeypatch) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from src.core.agents.type_of import type_of
    from src.core.config.load_settings import load_settings
    from src.ui.mixins import turn_mixin as module
    from src.ui.widgets.chat.chat_log import ChatLog

    async def run() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        session = store.create_session()
        trees = AgentTreeStore()
        trees.bind(session)
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()
        calls = 0

        async def stream(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            yield 'Done'

        async def review(model, snapshot, **kwargs):
            return GoalDecision(decision='complete', reason='Done')

        save = store.save_progress

        def save_progress(value, **kwargs):
            result = save(value, **kwargs)
            if value.goal is not None and value.goal.status == 'completed':
                finished.set()
            return result

        monkeypatch.setattr(store, 'save_progress', save_progress)
        monkeypatch.setattr(module, 'review_goal', review)
        app = SigilApp(
            Agent(FunctionModel(stream_function=stream)),
            store,
            session,
            agent_registry=AgentRegistry(),
            agent_tree=trees,
            settings=load_settings(),
        )
        async with app.run_test(size=(110, 45)) as pilot:
            app._meter.set_window(100_000)
            app.start_turn('Start work')
            await asyncio.wait_for(started.wait(), 5)
            await app.handle_goal('--yolo Ship CSV', app.query_one(ChatLog))
            release.set()
            await asyncio.wait_for(finished.wait(), 10)
            await pilot.pause()
            updates = [
                type_of(message, part)
                for message in session.messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and 'Ship CSV' in str(part.content)
            ]
            assert updates == [{'type': 'internal_notice'}]

    asyncio.run(run())
