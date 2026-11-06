"""Managing subagents from the UI: the tree is where they are backgrounded and stopped.

The two verbs the model has as tools are the two a user needs on a call that is
blocking the turn: `b` stops waiting without stopping the work, and `k` stops the
work. Both act on the ids the list shows, which are the ids the tools take.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from rich.text import Text
from textual.widgets import OptionList

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.agent_tree_screen import AgentTreeScreen, TreeAction, node_row, state_of, status_text
from src.ui.widgets.chat_log import ChatLog
from tests.ui_helpers import render_text


def _app(tmp: str, *, summoner: bool = False) -> SigilApp:
    """An app with a tree. With ``summoner``, one bound to that same tree.

    The summoner has to look at the tree the app shows: it resolves ids through the
    store it was given, and a second store would make every id unknown.
    """
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    registry = AgentRegistry([('builtin', Path(tmp) / 'none')], active=None)
    trees = AgentTreeStore()
    trees.bind(session, root_name='wizard')
    manager = (
        SubagentSummoner(trees=trees, registry=registry, builder=lambda: lambda profile: None) if summoner else None
    )
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        agent_registry=registry,
        agent_tree=trees,
        summoner=manager,
    )


def _running_node(app: SigilApp, *, mode: str = 'sync') -> Any:
    tree = app._agent_tree.active
    tree.ensure_root('wizard')
    node = tree.add(parent_id=tree.root_id, name='worker', role=AgentRole.SUBAGENT, task='read it', mode=mode)
    node.runs = 1
    return node


# --- the row and the header know the new state --------------------------------


def test_a_node_that_used_up_its_steps_says_so_in_its_row():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=AgentRole.SUBAGENT, task='a')
    node.finish('half a report', exhausted=True)

    assert state_of(node) == 'out of steps'
    row = render_text(node_row(node).prompt)
    assert 'out of steps' in row


def test_a_row_shows_how_many_times_the_node_ran():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=AgentRole.SUBAGENT, task='a')
    node.runs = 3

    row = render_text(node_row(node).prompt)
    assert 'run 3' in row


def test_the_status_line_counts_the_background():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    tree.add(parent_id=root.id, name='a', role=AgentRole.SUBAGENT, task='x', mode='async')
    tree.add(parent_id=root.id, name='b', role=AgentRole.SUBAGENT, task='y')

    status = render_text(Text.from_markup(status_text(tree.nodes())))
    assert '2 subagents' in status
    assert '2 running' in status
    assert '1 in the background' in status


# --- the actions --------------------------------------------------------------


def test_the_screen_returns_what_to_do_with_the_highlighted_row():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=AgentRole.SUBAGENT, task='a')
    screen = AgentTreeScreen(tree.nodes())

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.push_screen(screen)
                for _ in range(20):
                    await pilot.pause()
                    if app.screen is screen:
                        break
                screen.query_one(OptionList).highlighted = 1
                decided: list[Any] = []
                screen.dismiss = decided.append  # type: ignore[method-assign]
                screen.action_detach()
                for _ in range(20):
                    await pilot.pause()
                return decided[0]

    result = asyncio.run(inner())
    assert isinstance(result, TreeAction)
    assert result.kind == 'detach'
    assert result.node_id == node.id


def test_b_backgrounds_a_running_node():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, summoner=True)
            node = _running_node(app, mode='sync')
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                ok = await app.send_to_background(node.id, app.query_one(ChatLog))
                await pilot.pause()
                assert ok
                assert node.mode == 'async'
                assert any('background' in render_text(child.render()) for child in app.query_one(ChatLog).children)

    asyncio.run(inner())


def test_k_stops_the_node():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, summoner=True)
            node = _running_node(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                ok = await app.stop_node(node.id, app.query_one(ChatLog))
                await pilot.pause()
                assert ok
                assert node.status is NodeStatus.CANCELLED
                assert any('stopped' in render_text(child.render()) for child in app.query_one(ChatLog).children)

    asyncio.run(inner())


def test_a_refusal_from_the_summoner_is_a_line_and_not_a_crash():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, summoner=True)
            tree = app._agent_tree.active
            tree.ensure_root('wizard')
            node = tree.add(parent_id=tree.root_id, name='worker', role=AgentRole.SUBAGENT, task='a')
            node.finish('done')
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # Already finished: nothing to background, and the reason is read.
                ok = await app.send_to_background(node.id, app.query_one(ChatLog))
                await pilot.pause()
                assert ok is False
                assert any(
                    'already finished' in render_text(child.render()) for child in app.query_one(ChatLog).children
                )

    asyncio.run(inner())


def test_alt_t_then_b_backgrounds_without_opening_the_node():
    """The whole path: the key, the list, `b` — and the chat stays where it was."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, summoner=True)
            node = _running_node(app, mode='sync')
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press('ctrl+t')
                for _ in range(30):
                    await pilot.pause()
                    if isinstance(app.screen, AgentTreeScreen):
                        break
                assert isinstance(app.screen, AgentTreeScreen)
                app.screen.query_one(OptionList).highlighted = 1
                await pilot.press('b')
                for _ in range(30):
                    await pilot.pause()
                    if not isinstance(app.screen, AgentTreeScreen):
                        break
                assert node.mode == 'async'
                assert app.subagent_open is False

    asyncio.run(inner())
