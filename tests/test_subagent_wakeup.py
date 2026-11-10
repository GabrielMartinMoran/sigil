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

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
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


# --- the engine side ---------------------------------------------------------


def test_an_answer_with_nobody_in_a_turn_stays_undelivered():
    tree = AgentTreeStore()
    with tempfile.TemporaryDirectory() as tmp:
        summoner = _summoner(tmp, tree)
        session = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions').create_session()
        tree.bind(session, root_name='wizard')
        tree.active.ensure_root('wizard')
        node = tree.active.add(parent_id=tree.active.root_id, name='researcher', role=AgentRole.SUBAGENT)
        node.finish('done')
        node.delivered = False

        assert [candidate.id for candidate in summoner.waiting()] == [node.id]
        messages = summoner.take_messages()
        assert len(messages) == 1 and 'done' in messages[0]
        # Consumed: a second turn does not read it again.
        assert summoner.waiting() == []
        assert summoner.take_messages() == []


def test_the_undelivered_flag_survives_a_save_and_a_load(tmp_path: Path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    tree = AgentTreeStore()
    trees = tree.bind(session, root_name='wizard')
    node = trees.add(parent_id=trees.root_id, name='researcher', role=AgentRole.SUBAGENT, task='read')
    node.finish('a report')
    node.delivered = False
    store.save(session)

    loaded = store.load(session.id)

    assert loaded is not None and loaded.tree is not None
    restored = loaded.tree.node(node.id)
    assert restored is not None
    assert restored.delivered is False
    assert restored.result == 'a report'


# --- the app side ------------------------------------------------------------


def test_a_finished_subagent_wakes_the_session():
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


def test_three_reports_in_a_row_produce_one_wake_up():
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


def test_a_wake_up_does_not_steal_a_turn_the_user_started():
    """If the user typed first, that turn drains the reports at its top."""

    async def inner():
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


def test_the_chat_says_the_turn_was_woken_and_not_typed():
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


def test_a_summon_renders_as_a_spawn_and_not_as_a_tool_call():
    from src.ui.widgets.chat_log import SummonBlock, block_for

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


def test_a_resumed_summon_says_so():
    from src.ui.widgets.chat_log import SummonBlock

    block = SummonBlock('summon', {'agent': 'scout', 'task': 'keep going', 'agent_id': 'abcdef1234'})
    text = _text_of(block)
    assert 'resume' in text


def test_an_exhausted_subagent_reads_as_out_of_steps():
    from src.ui.widgets.chat_log import SummonBlock

    block = SummonBlock('summon', {'agent': 'scout', 'task': 'go', 'mode': 'sync'})
    block.set_result('[subagent Scout (scout #abcd1234, sync) out of steps]\npartial')
    assert 'out of steps' in _text_of(block)


def test_a_refused_summon_reads_as_refused():
    from src.ui.widgets.chat_log import SummonBlock

    block = SummonBlock('summon', {'agent': 'nobody', 'task': 'go'})
    block.set_error('Unknown subagent')
    assert 'refused' in _text_of(block)


def test_a_plain_tool_is_still_a_plain_block():
    from src.ui.widgets.chat_log import ToolCallBlock, block_for

    assert type(block_for('read_file', {'path': 'x'}, classes='tool-call')) is ToolCallBlock


def _unused(value: Any) -> Any:  # pragma: no cover
    return value


def _model_handler() -> FunctionModel:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart('ok')])

    return FunctionModel(model)


def _unused_tool_part() -> ToolCallPart:  # pragma: no cover
    return ToolCallPart('summon', {})


def _unused_status() -> NodeStatus:  # pragma: no cover
    return NodeStatus.DONE


def _text_of(block: Any) -> str:
    """The plain text of a composed block: the Group it builds, line by line."""
    rendered = block._build()
    return chr(10).join(part.plain for part in rendered.renderables)
