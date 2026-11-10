"""Clicking a subagent in the chat opens it.

The spawn block is the second way into a subagent — `ctrl+t` is the first — and it is
the obvious one while reading the conversation: the block already says which
execution it started, so clicking it is what a reader expects.

What has to hold:

- a block that knows its node opens it on click, in the app;
- a block that does not (a spawn still running) is not clickable, and a click on it
  is a no-op rather than an error;
- the id the block shows is the short one, and it resolves;
- a click on a node this session no longer has says so instead of doing nothing.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.messages import NodeOpened
from src.ui.widgets.agent_view import AgentView
from src.ui.widgets.chat_log import ChatLog, SummonBlock
from tests.ui_helpers import render_text, wait_until


def _app(tmp: str, *, with_summoner: bool = True) -> SigilApp:
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
        summoner=summoner if with_summoner else None,
    )


def _node(app: SigilApp, *, finished: bool = True) -> Any:
    tree = app._agent_tree.active
    tree.ensure_root('wizard')
    node = tree.add(
        parent_id=tree.root_id,
        name='researcher',
        role=AgentRole.SUBAGENT,
        task='read the tree',
    )
    if finished:
        node.finish('it has three modules')
    return node


def _spawn_block(app: SigilApp, node: Any, *, with_id: bool = True) -> SummonBlock:
    block = SummonBlock('summon', {'agent': 'researcher', 'task': 'read the tree', 'mode': 'async'})
    if with_id:
        block.set_result(f'[subagent Researcher (researcher #{node.short_id}, async) finished]')
    return block


# --- the block itself --------------------------------------------------------


def _text_of(block: SummonBlock) -> str:
    return chr(10).join(part.plain for part in block._build().renderables)


def test_a_block_that_knows_its_node_offers_a_way_in():
    block = SummonBlock('summon', {'agent': 'researcher', 'task': 'read it', 'mode': 'async'})
    assert 'clickable' not in block.classes

    block.set_result('[subagent Researcher (researcher #abcd1234, async) finished]')

    assert 'clickable' in block.classes
    assert '#abcd1234' in _text_of(block)


def test_a_block_still_running_is_not_clickable():
    """No id yet means no target: the click has to be inert, not guess."""
    block = SummonBlock('summon', {'agent': 'researcher', 'task': 'read it', 'mode': 'async'})
    assert 'clickable' not in block.classes
    # And clicking it does nothing rather than raising.
    block.on_click()


def test_the_block_underlines_the_name_only_when_it_is_a_target():
    block = SummonBlock('summon', {'agent': 'researcher', 'task': 'read it'})
    before = _text_of(block)

    block.set_result('[subagent Researcher (researcher #abcd1234, sync) finished]')
    after = _text_of(block)

    assert 'Researcher' in before and 'Researcher' in after
    # The underline is a style, so it does not change the text: what changes is the
    # class that makes the hover visible.
    assert 'clickable' in block.classes


# --- the app -----------------------------------------------------------------


def test_clicking_a_spawn_opens_the_subagent():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                node = _node(app)
                block = _spawn_block(app, node)
                await app.query_one(ChatLog).mount(block)
                await pilot.pause()

                await pilot.click(block)
                for _ in range(30):
                    await pilot.pause()
                    if app.subagent_open:
                        break
                return app.subagent_open, app.viewed_node_id(), node.id

    opened, viewed, expected = asyncio.run(inner())
    assert opened is True
    assert viewed == expected


def test_the_click_resolves_the_short_id_the_block_shows():
    """The block paints eight characters and the tree stores thirty-two."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                node = _node(app)
                block = _spawn_block(app, node)
                await app.query_one(ChatLog).mount(block)
                await pilot.pause()

                assert block._node_id == node.short_id
                await pilot.click(block)
                for _ in range(30):
                    await pilot.pause()
                    if app.subagent_open:
                        break
                return app.subagent_open

    assert asyncio.run(inner()) is True


def test_a_click_on_a_node_this_session_lost_says_so():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                app.post_message(NodeOpened('deadbeef'))
                await wait_until(
                    pilot,
                    lambda: any(
                        'not part of this session' in render_text(child.render())
                        for child in app.query_one(ChatLog).children
                    ),
                )
                return [render_text(child.render()) for child in app.query_one(ChatLog).children]

    texts = asyncio.run(inner())
    assert any('not part of this session' in text for text in texts)


def test_a_click_does_not_open_anything_while_the_block_has_no_id():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                _node(app)
                block = _spawn_block(app, None, with_id=False)
                await app.query_one(ChatLog).mount(block)
                await pilot.pause()
                await pilot.click(block)
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
                return app.subagent_open

    assert asyncio.run(inner()) is False


def test_opening_by_click_also_restores_the_chat_on_escape():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                node = _node(app)
                block = _spawn_block(app, node)
                await app.query_one(ChatLog).mount(block)
                await pilot.pause()
                await pilot.click(block)
                for _ in range(30):
                    await pilot.pause()
                    if app.subagent_open:
                        break

                await pilot.press('escape')
                for _ in range(30):
                    await pilot.pause()
                    if not app.subagent_open:
                        break
                return app.subagent_open, app.query_one(AgentView).display

    opened, shown = asyncio.run(inner())
    assert opened is False
    assert shown is False


def test_only_the_spawn_block_is_clickable():
    """The rest of the chat stays text: a file read is not a way in."""
    from src.ui.widgets.chat_log import block_for

    plain = block_for('read_file', {'path': 'x'}, classes='tool-call')
    plain.set_result('contents')

    assert 'clickable' not in plain.classes
    assert not isinstance(plain, SummonBlock)
