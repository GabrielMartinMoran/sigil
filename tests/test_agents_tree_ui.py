"""The tree in the UI: `ctrl+t`, opening a subagent, going back, and the messages.

What is pinned here is the part a user can see: the list of agents, the view of one
node with the composer locked, the way back to the parent, and the message an async
subagent sends that nobody was there to read.

Nothing here drives a real model: the tree is filled by hand, which is the state
the UI has to paint anyway, and it is the state a resumed session comes back with.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.test import TestModel
from rich.text import Text
from textual.widgets import OptionList

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.agent_tree_screen import AgentTreeScreen, node_row, status_text
from src.ui.widgets.agent_view import AgentView, build_agent_header, elapsed_text
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from tests.ui_helpers import render_text


def _app(tmp: str, *, summoner: Any = None) -> SigilApp:
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
        summoner=summoner,
    )


def _fill(app: SigilApp, *, running: str = '') -> Any:
    """Two subagents under the root: one done and, optionally, one working.

    It returns the one the caller is about to look at: the running one when it asks
    for one, the finished first node otherwise.
    """
    tree = app._agent_tree.active
    tree.ensure_root('wizard')
    first = tree.add(parent_id=tree.root_id, name='researcher', role=AgentRole.SUBAGENT, task='read the tree')
    if running == 'researcher':
        return first
    first.finish('it has three modules')
    second = tree.add(parent_id=tree.root_id, name='reviewer', role=AgentRole.SUBAGENT, task='review it')
    if running == 'reviewer':
        return second
    second.finish('looks fine')
    return first


# --- the node view -----------------------------------------------------------


def test_the_header_says_where_the_node_is_and_how_it_went():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='deep-researcher', role=AgentRole.SUBAGENT, task='read\n  the  tree')
    node.finish('done')

    text = render_text(build_agent_header(node, parent_label='Wizard', has_parent=True))

    assert 'Esc returns to Wizard' in text
    assert 'Deep Researcher' in text
    assert 'done' in text
    assert 'sync' in text
    # The task is *not* repeated: it is the first message of the transcript below,
    # and the header stays fixed while that transcript scrolls.
    assert 'read the tree' not in text


def test_a_failed_node_shows_why():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=AgentRole.SUBAGENT, task='go')
    node.fail('RuntimeError: the provider exploded')

    text = render_text(build_agent_header(node, parent_label='Wizard', has_parent=True))
    assert 'failed' in text
    assert 'the provider exploded' in text


def test_elapsed_is_short_and_readable():
    assert elapsed_text(0.4) == '0.4s'
    assert elapsed_text(75) == '1m15s'
    assert elapsed_text(3700) == '1h01m'


# --- the tree screen ---------------------------------------------------------


def test_a_row_indents_by_depth_and_names_the_state():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='researcher', role=AgentRole.SUBAGENT, task='read it')
    node.finish('ok')
    child = tree.add(parent_id=node.id, name='reader', role=AgentRole.SUBAGENT, task='deeper')
    child.finish('ok')

    line = render_text(node_row(child, viewed='').prompt)
    assert line.startswith('    ')  # two levels of indentation
    assert 'Reader' in line


def test_the_status_line_counts_the_subagents_and_what_is_running():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    done = tree.add(parent_id=root.id, name='researcher', role=AgentRole.SUBAGENT, task='a')
    done.finish('ok')
    tree.add(parent_id=done.id, name='reader', role=AgentRole.SUBAGENT, task='b')

    status = render_text(Text.from_markup(status_text(tree.nodes())))
    assert '2 subagents' in status
    assert '1 running' in status


def test_a_tree_with_only_the_root_says_there_is_nobody_yet():
    tree = AgentTreeStore().active
    tree.ensure_root('wizard')
    assert 'No subagents' in render_text(Text.from_markup(status_text(tree.nodes())))


# --- navigation --------------------------------------------------------------


def test_alt_t_opens_the_tree_and_choosing_a_node_opens_it():
    """The whole path a user takes: the key, the list, Enter, the node."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            first = _fill(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press('ctrl+t')
                for _ in range(30):
                    await pilot.pause()
                    if isinstance(app.screen, AgentTreeScreen):
                        break
                assert isinstance(app.screen, AgentTreeScreen)
                screen = app.screen
                assert len(screen._rows) == 3

                # Highlight the subagent and open it.
                screen.query_one(OptionList).highlighted = 1
                await pilot.press('enter')
                for _ in range(30):
                    await pilot.pause()
                    if app.subagent_open:
                        break
                assert app.viewed_node_id() == first.id
                assert app.query_one(AgentView).display is True

    asyncio.run(inner())


def test_opening_a_subagent_replaces_the_chat_and_locks_the_composer():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            first = _fill(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.open_subagent(first)
                await pilot.pause()

                assert app.query_one(AgentView).display is True
                assert app.query_one(ChatLog).display is False
                assert app.query_one(PromptInput).disabled is True
                assert app.subagent_open is True
                assert app.viewed_node_id() == first.id

                # Esc is the way back to the conversation, and it has to work with
                # the composer disabled: the key is the only way out.
                await pilot.press('escape')
                for _ in range(20):
                    await pilot.pause()
                    if not app.subagent_open:
                        break
                assert app.query_one(AgentView).display is False
                assert app.query_one(ChatLog).display is True
                assert app.query_one(PromptInput).disabled is False
                assert app.viewed_node_id() is None

    asyncio.run(inner())


def test_the_node_view_paints_the_transcript_of_the_node():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            first = _fill(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.open_subagent(first)
                for _ in range(20):
                    await pilot.pause()
                view = app.query_one(AgentView)
                header = render_text(view.query_one('#agent-header').render())
                assert 'Researcher' in header
                # The task lives in the transcript, not pinned above it: the header
                # stays fixed while the transcript scrolls, so carrying it twice
                # would push the work down the screen for good.
                assert 'read the tree' not in header

                # A real node keeps its own transcript, starting with the task.
                first.messages = [ModelRequest(parts=[UserPromptPart(content='read the tree')])]
                await view.refresh_node(first)
                for _ in range(20):
                    await pilot.pause()
                rendered = '\n'.join(render_text(child.render()) for child in view.transcript.children)
                assert 'read the tree' in rendered
                assert 'read the tree' not in render_text(view.query_one('#agent-header').render())

    asyncio.run(inner())


def test_going_back_from_a_nested_node_lands_on_its_parent():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            first = _fill(app)
            tree = app._agent_tree.active
            child = tree.add(parent_id=first.id, name='reader', role=AgentRole.SUBAGENT, task='deeper')
            child.finish('ok')
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.open_subagent(child)
                await pilot.pause()
                assert app.viewed_node_id() == child.id

                await app.open_parent()
                await pilot.pause()
                assert app.viewed_node_id() == first.id

                await app.open_parent()
                await pilot.pause()
                assert app.viewed_node_id() is None

    asyncio.run(inner())


def test_alt_t_without_subagents_says_so_instead_of_opening_an_empty_box():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # Nobody to open yet: the app says so instead of showing a box with
                # the one row the user already knows.
                await app.open_tree()
                await pilot.pause()
                chat = app.query_one(ChatLog)
                assert any('No subagents' in render_text(child.render()) for child in chat.children)
                assert not isinstance(app.screen, AgentTreeScreen)

    asyncio.run(inner())


def test_opening_the_debug_view_closes_the_node_view():
    """Two views that replace the same chat cannot both be on screen."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            first = _fill(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.open_subagent(first)
                await pilot.pause()
                assert app.subagent_open is True

                app.action_toggle_debug()
                await pilot.pause()

                assert app.subagent_open is False
                assert app.debug_visible is True
                assert app.query_one(ChatLog).display is False

    asyncio.run(inner())


def test_new_session_while_a_subagent_is_open_gives_the_chat_back():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            first = _fill(app)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.open_subagent(first)
                await pilot.pause()

                await app.new_session(app.query_one(ChatLog))
                await pilot.pause()

                assert app.subagent_open is False
                assert app.query_one(PromptInput).disabled is False
                assert app.viewed_node_id() is None

    asyncio.run(inner())


def test_a_finished_subagent_repaints_the_view_it_is_shown_in():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            first = _fill(app, running='researcher')
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await app.open_subagent(first)
                await pilot.pause()
                assert 'running' in render_text(app.query_one('#agent-header').render())

                first.finish('it has three modules')
                app.announce_tree_change(first)
                for _ in range(20):
                    await pilot.pause()
                assert 'done' in render_text(app.query_one('#agent-header').render())

    asyncio.run(inner())


# --- the way back for async subagents ----------------------------------------


def test_a_message_from_a_subagent_goes_to_the_running_turn():
    """The run is the one the app owns; the summoner asks for it, it does not keep it."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                assert app.root_run() is None

                enqueued: list[str] = []

                class Run:
                    def enqueue(self, text: str) -> str:
                        enqueued.append(text)
                        return 'id'

                app._turn_active = True
                app._current_run = Run()
                assert app.root_run() is not None
                app.announce_subagent_message(
                    app._agent_tree.active.add(
                        parent_id=app._agent_tree.active.root_id,
                        name='worker',
                        role=AgentRole.SUBAGENT,
                        task='go',
                    )
                )
                for _ in range(20):
                    await pilot.pause()
                chat = app.query_one(ChatLog)
                assert any('sent its message' in render_text(child.render()) for child in chat.children)
                app._turn_active = False
                app._current_run = None

    asyncio.run(inner())


def test_the_messages_that_arrive_while_idle_wait_for_the_next_turn():
    """`pending` is drained by the turn, not by the UI: it is the engine's queue."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            registry = AgentRegistry([('builtin', Path(tmp) / 'none')], active=None)
            summoner = SubagentSummoner(
                trees=AgentTreeStore(),
                registry=registry,
                builder=lambda: lambda profile: None,
            )
            app = _app(tmp, summoner=summoner)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                assert app.take_subagent_messages() == []
                assert app._summoner is summoner
                # The listener was attached on mount, pointing at the app.
                assert summoner._root_run == app.root_run

    asyncio.run(inner())


def test_stop_subagents_reaches_the_summoner():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            registry = AgentRegistry([('builtin', Path(tmp) / 'none')], active=None)
            summoner = SubagentSummoner(
                trees=AgentTreeStore(),
                registry=registry,
                builder=lambda: lambda profile: None,
            )
            app = _app(tmp, summoner=summoner)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                called: list[bool] = []
                summoner.cancel_all = lambda: called.append(True)  # type: ignore[method-assign]
                app.stop_subagents()
                assert called == [True]

    asyncio.run(inner())


def test_loading_another_session_swaps_the_tree():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            _fill(app)
            first_tree = app._agent_tree.active
            assert len(first_tree) == 3

            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                other = app._store.create_session()
                app.bind_session(other)
                await app.query_one(ChatLog).clear()
                assert app._agent_tree.active is not first_tree
                assert len(app._agent_tree.active) == 1
                assert app.viewed_node_id() is None

    asyncio.run(inner())


def test_running_node_keeps_unreturned_calls_pending_and_finishes_them_on_cancel():
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from src.ui.widgets.chat_log import ToolCallBlock

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            node = _fill(app, running='researcher')
            node.messages = [ModelResponse(parts=[ToolCallPart('read_file', {}, tool_call_id='a')])]
            async with app.run_test() as pilot:
                await app.open_subagent(node)
                await pilot.pause()
                view = app.query_one(AgentView)
                assert view.transcript.query_one(ToolCallBlock).pending
                node.cancel()
                await view.refresh_node(node)
                tool = view.transcript.query_one(ToolCallBlock)
                assert not tool.pending
                assert 'Interrupted' in tool._error

    asyncio.run(inner())


def test_overlapping_refreshes_do_not_mix_transcript_snapshots():
    from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

    from src.ui.widgets.chat_log import ToolCallBlock

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            node = _fill(app, running='researcher')
            async with app.run_test():
                await app.open_subagent(node)
                view = app.query_one(AgentView)
                original = view.transcript.render_history
                started = asyncio.Event()
                release = asyncio.Event()
                calls = 0

                async def delayed(messages: list, **kwargs: Any) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        started.set()
                        await release.wait()
                    await original(messages, **kwargs)

                view.transcript.render_history = delayed
                node.messages = [ModelResponse(parts=[ToolCallPart('read_file', {}, tool_call_id='a')])]
                first = asyncio.create_task(view.refresh_node(node))
                await started.wait()
                node.messages = [
                    *node.messages,
                    ModelRequest(parts=[ToolReturnPart('read_file', 'ok', tool_call_id='a')]),
                ]
                second = asyncio.create_task(view.refresh_node(node))
                await asyncio.sleep(0)
                release.set()
                await asyncio.gather(first, second)
                tools = list(view.transcript.query(ToolCallBlock))
                assert len(tools) == 1
                assert tools[0]._result == 'ok'
                assert not tools[0].pending

    asyncio.run(inner())


def test_node_view_refreshes_when_a_message_changes_without_a_count_change():
    from pydantic_ai.messages import ModelResponse, TextPart

    from src.ui.widgets.chat_log import AssistantBlock

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            node = _fill(app, running='researcher')
            node.messages = [ModelResponse(parts=[TextPart('before')])]
            async with app.run_test():
                await app.open_subagent(node)
                view = app.query_one(AgentView)
                node.messages[0].parts[0].content = 'after'
                await view.refresh_node(node)
                assert 'after' in render_text(view.transcript.query_one(AssistantBlock)._build())

    asyncio.run(inner())


def test_subagent_instructions_are_distinct_from_user_messages():
    from src.ui.widgets.chat_log import AgentMessageBlock, UserMessage

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            node = _fill(app, running='researcher')
            node.messages = [ModelRequest(parts=[UserPromptPart('Investigate the project')])]
            async with app.run_test():
                await app.open_subagent(node)
                view = app.query_one(AgentView)
                instruction = view.transcript.query_one(AgentMessageBlock)
                assert 'Investigate the project' in render_text(instruction.render())
                assert 'agent-msg' in instruction.classes
                assert not list(view.transcript.query(UserMessage))

    asyncio.run(inner())
