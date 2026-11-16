"""A finished subagent wakes the agent that summoned it.

The turn that dispatched the squad ends — the user read its answer and the session
went back to idle — and the subagent's result arrives afterwards. Waiting for the
user to type something would leave the squad's work unread, so the app starts the
turn the answer is owed, and it is exactly one turn no matter how many reports land
inside the debounce window.

Three things are pinned here:

- the session wakes on its own, with the reports prepended;
- three reports inside the window produce **one** turn, not three;
- a report that arrives while the app is closed is not lost: the flag that says
  "nobody read this" travels with the session.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.core.agents.profile.agent_registry import AgentRegistry
from src.core.agents.profile.models.agent_role import AgentRole
from src.core.agents.tree.agent_tree_store import AgentTreeStore
from src.core.agents.tree.subagent_summoner import SubagentSummoner
from src.core.sessions.session_store import SessionStore
from src.ui.attention import Attention
from src.ui.sigil_app import SigilApp
from src.ui.widgets.chat_log import ChatLog


def _app(tmp: str, *, with_summoner: bool = False) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    registry = AgentRegistry([('builtin', Path(tmp) / 'none')], active=None)
    trees = AgentTreeStore()
    trees.bind(session, root_name='wizard')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        agent_registry=registry,
        agent_tree=trees,
        summoner=_summoner(tmp, trees) if with_summoner else None,
    )


def _summoner(tmp: str, tree: AgentTreeStore) -> SubagentSummoner:
    registry = AgentRegistry([('builtin', Path(tmp) / 'none')], active=None)
    return SubagentSummoner(trees=tree, registry=registry, builder=lambda: lambda profile: None)


def _finished_node(app: SigilApp, *, result: str = 'I read the tree.') -> Any:
    """A subagent that finished with nobody in a turn."""
    tree = app._agent_tree.active
    tree.ensure_root('wizard')
    node = tree.add(parent_id=tree.root_id, name='researcher', role=AgentRole.SUBAGENT, task='read it')
    node.mode = 'async'
    node.finish(result)
    node.delivered = False
    return node


# --- the app side ------------------------------------------------------------


def test_a_finished_subagent_wakes_the_session() -> None:
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, with_summoner=True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                node = _finished_node(app)
                # `subagents_idle` arms a debounce; the wake itself is the timer's
                # callback, so the test drives it and then lets the turn run.
                app.subagents_idle()
                assert app._wake_timer is not None
                app._wake_from_subagents()
                for _ in range(60):
                    await pilot.pause()
                    if node.delivered and not app._turn_active:
                        break
                return app, node

    _app_used, node = asyncio.run(inner())
    assert node.delivered is True, 'the woken turn consumed the report'


def test_three_reports_in_a_row_produce_one_wake_up() -> None:
    """A squad reports within a second: one turn reads all three."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, with_summoner=True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                tree = app._agent_tree.active
                tree.ensure_root('wizard')
                nodes = []
                for _ in range(3):
                    node = tree.add(parent_id=tree.root_id, name='researcher', role=AgentRole.SUBAGENT, task='read it')
                    node.mode = 'async'
                    node.finish('a report')
                    node.delivered = False
                    nodes.append(node)
                    # Each report asks for a wake; the timer is already armed.
                    app.subagents_idle()
                assert app._wake_timer is not None
                armed = app._wake_timer
                app.subagents_idle()
                assert app._wake_timer is armed, 'the timer is not re-armed while it is pending'
                app._wake_from_subagents()
                for _ in range(60):
                    await pilot.pause()
                    if all(node.delivered for node in nodes):
                        break
                return nodes

    nodes = asyncio.run(inner())
    assert all(node.delivered for node in nodes)


def test_a_wake_up_does_not_steal_a_turn_the_user_started() -> None:
    """If the user typed first, that turn drains the reports at its top."""

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, with_summoner=True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                node = _finished_node(app)
                app._turn_active = True
                app._wake_from_subagents()
                await pilot.pause()
                # No new turn was started, and the report is still waiting.
                assert node.delivered is False
                assert app._wake_timer is None
                app._turn_active = False

    asyncio.run(inner())


def test_the_chat_says_the_turn_was_woken_and_not_typed() -> None:
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, with_summoner=True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                _finished_node(app)
                app._wake_pending = True
                app.start_turn('placeholder')
                for _ in range(60):
                    await pilot.pause()
                    if not app._turn_active:
                        break
                chat = app.query_one(ChatLog)
                return [str(child.render()) for child in chat.children]

    texts = asyncio.run(inner())
    assert any('subagent report' in text for text in texts)


# --- the visual block --------------------------------------------------------


def test_a_summon_renders_as_a_spawn_and_not_as_a_tool_call() -> None:
    from src.ui.widgets.block_for import block_for
    from src.ui.widgets.summon_block import SummonBlock

    block = block_for(
        'summon',
        {'agent': 'pulsar-researcher', 'task': 'read the tree end to end', 'mode': 'async'},
        classes='tool-call',
    )
    assert isinstance(block, SummonBlock)
    block.set_result('#1a2b3c4d (Pulsar Researcher) is working in the background.')
    text = _text_of(block)

    assert 'Pulsar Researcher' in text
    assert 'read the tree end to end' in text
    assert 'async' in text and 'working' in text
    assert '#1a2b3c4d'[:9] in text
    # It is not the JSON blob of a generic tool call.
    assert '{"agent"' not in text


def test_a_resumed_summon_says_so() -> None:
    from src.ui.widgets.summon_block import SummonBlock

    block = SummonBlock('summon', {'agent': 'scout', 'task': 'keep going', 'agent_id': 'abcdef1234'})
    text = _text_of(block)
    # The id in parentheses after the name is what a resume adds to a spawn: it is
    # the execution that continues, and no fresh hand-off carries one.
    assert 'Scout (#abcdef12)' in text


def test_a_resume_paints_the_id_once() -> None:
    """The header carries the id of a resume; the meta line does not repeat it."""
    from src.ui.widgets.summon_block import SummonBlock

    block = SummonBlock('summon', {'task': 'keep going', 'agent_id': 'abcdef1234', 'mode': 'async'})
    block.set_result('#abcdef1234 (Scout) is working in the background.')
    text = _text_of(block)

    assert text.count('#abcdef12') == 1
    assert 'async · working' in block._meta_line().plain


def test_an_exhausted_subagent_reads_as_out_of_steps() -> None:
    from src.ui.widgets.summon_block import SummonBlock

    block = SummonBlock('summon', {'agent': 'scout', 'task': 'go', 'mode': 'sync'})
    block.set_result('[subagent Scout (scout #abcd1234, sync) out of steps]\npartial')
    assert 'out of steps' in _text_of(block)


def test_a_refused_summon_reads_as_refused() -> None:
    from src.ui.widgets.summon_block import SummonBlock

    block = SummonBlock('summon', {'agent': 'nobody', 'task': 'go'})
    block.set_error('Unknown subagent')
    assert 'refused' in _text_of(block)


def test_the_notification_waits_for_the_last_subagent() -> None:
    """The end of the turn is not the end of the work: the last report is.

    The turn that dispatched the squad ends while the subagent keeps working, and
    notifying then would call the user back to a screen that is still busy. The
    wake-up turn that reads the report is the one that has something to announce.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, with_summoner=True)
            app.notify_attention = Mock()
            running = {'count': 1}
            app.subagents_running = lambda: running['count']
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.run_turn('dispatch the researcher')
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert app.notify_attention.call_args_list == [], 'notified while a subagent ran'
                # The subagent finishes: the app wakes, and that turn ends quiet.
                running['count'] = 0
                node = _finished_node(app)
                app.subagents_idle()
                app._wake_from_subagents()
                for _ in range(60):
                    await pilot.pause()
                    if node.delivered and not app._turn_active:
                        break
            return app

    app = asyncio.run(inner())
    app.notify_attention.assert_called_once_with(Attention.FINISHED)


def test_a_plain_tool_is_still_a_plain_block() -> None:
    from src.ui.widgets.block_for import block_for
    from src.ui.widgets.tool_call_block import ToolCallBlock

    assert type(block_for('read_file', {'path': 'x'}, classes='tool-call')) is ToolCallBlock


def _text_of(block: Any) -> str:
    """The plain text of a composed block: the Group it builds, line by line."""
    rendered = block._build()
    return chr(10).join(part.plain for part in rendered.renderables)
