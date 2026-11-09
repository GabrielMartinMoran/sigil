"""Rendering the history has to scale with the conversation, not quadratically.

The regression these cover: `render_history` mounted one widget per message and
let Textual lay out after every one, so restoring a long conversation cost a full
reflow per message — about 5 seconds for a 1,400-message session, on the startup
path, before the window was usable.

The fix is to build the widgets first and mount them in one batch, which makes the
layout happen once. These tests pin the shape of the cost, not a wall-clock number:
they compare a long history against a short one on the same machine.
"""

from __future__ import annotations

import asyncio
import time

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from textual.app import App, ComposeResult

from src.ui.widgets.chat_log import ChatLog, ToolCallBlock, ToolGroupHeader


def history(turns: int) -> list:
    """A conversation of `turns` user/assistant pairs, similar to a real one."""
    messages: list = []
    for index in range(turns):
        messages.append(ModelRequest(parts=[UserPromptPart(content=f'question {index} ' + 'x' * 80)]))
        messages.append(ModelResponse(parts=[TextPart(content=f'answer {index} ' + 'y' * 200)]))
    return messages


class Harness(App):
    def compose(self) -> ComposeResult:
        yield ChatLog(id='chat')


def render(turns: int) -> float:
    async def inner() -> float:
        app = Harness()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chat = app.query_one(ChatLog)
            started = time.perf_counter()
            await chat.render_history(history(turns))
            chat.finalize()
            return time.perf_counter() - started

    return asyncio.run(inner())


def test_rendering_a_long_history_is_not_quadratic():
    """The cost must grow with the number of messages, not with its square.

    Mounting one widget at a time reflows the whole log every time, so doubling the
    history used to quadruple the time. Batched, it roughly doubles.
    """
    small = render(150)
    large = render(600)

    # 4x the messages. Quadratic would be ~16x; allow up to 8x for noise, which is
    # still far below what the old per-message mount produced.
    assert large < small * 8, f'{small=:.3f}s {large=:.3f}s: the cost grows too fast'
    # And an absolute ceiling: a 1,200-message history has no business taking
    # seconds to restore.
    assert large < 2.5, f'restoring 1,200 messages took {large:.2f}s'


def test_the_history_renders_every_message():
    """Batching must not drop or reorder anything."""

    async def inner() -> None:
        app = Harness()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chat = app.query_one(ChatLog)
            await chat.render_history(history(20))
            chat.finalize()
            # One block per turn piece, in order: 20 user lines + 20 answers.
            assert len(chat.children) == 40
            users = [str(child.render()) for child in chat.children if 'user-msg' in child.classes]
            assert 'question 0' in users[0]
            assert 'question 19' in users[-1]

    asyncio.run(inner())


def test_a_run_of_tool_calls_is_spaced_the_same_live_and_restored():
    """Separate model responses keep their spacing in live and restored transcripts.

    Each response contains a single tool, so its header is hidden and the row must
    own the gap. The live fixture must begin each response just as the engine does.
    """

    def two_calls() -> list:
        return [
            ModelRequest(parts=[UserPromptPart(content='go')]),
            ModelResponse(parts=[ToolCallPart(tool_name='run', args={'command': 'ls'}, tool_call_id='1')]),
            ModelRequest(parts=[ToolReturnPart(tool_name='run', content='a.py', tool_call_id='1')]),
            ModelResponse(parts=[ToolCallPart(tool_name='read', args={'path': 'a.py'}, tool_call_id='2')]),
            ModelRequest(parts=[ToolReturnPart(tool_name='read', content='print(1)', tool_call_id='2')]),
            ModelResponse(parts=[TextPart(content='done')]),
        ]

    async def inner() -> None:
        app = Harness()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chat = app.query_one(ChatLog)

            await chat.render_history(two_calls())
            chat.finalize()
            restored = [child.classes for child in chat.children if isinstance(child, ToolCallBlock)]

            await chat.clear()
            for call, result in (('run', 'a.py'), ('read', 'print(1)')):
                await chat.begin_response()
                await chat.add_tool_call(call, {})
                await chat.add_tool_result(call, result)
            chat.finalize()
            live = [child.classes for child in chat.children if isinstance(child, ToolCallBlock)]

            assert restored == live
            headers = [child for child in chat.children if isinstance(child, ToolGroupHeader)]
            assert len(headers) == 2
            assert not any(header.display for header in headers)
            assert all('tight' not in classes for classes in restored)

    asyncio.run(inner())


def test_an_empty_history_is_a_no_op():
    async def inner() -> None:
        app = Harness()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chat = app.query_one(ChatLog)
            await chat.render_history([])
            assert not chat.children

    asyncio.run(inner())


def test_restored_parallel_tool_results_match_call_ids():
    async def inner() -> None:
        app = Harness()
        async with app.run_test() as pilot:
            chat = app.query_one(ChatLog)
            await chat.render_history(
                [
                    ModelResponse(
                        parts=[ToolCallPart('run', {}, tool_call_id='a'), ToolCallPart('run', {}, tool_call_id='b')]
                    ),
                    ModelRequest(
                        parts=[
                            ToolReturnPart('run', 'first', tool_call_id='a'),
                            ToolReturnPart('run', 'second', tool_call_id='b'),
                        ]
                    ),
                ]
            )
            await pilot.pause()
            tools = list(chat.query(ToolCallBlock))
            assert len(tools) == 2
            assert [tool.pending for tool in tools] == [False, False]
            assert [tool._result for tool in tools] == ['first', 'second']

    asyncio.run(inner())


def test_history_shows_opaque_reasoning_without_exposing_signature():
    from pydantic_ai.messages import ThinkingPart

    from src.ui.widgets.chat_log import AssistantBlock
    from tests.ui_helpers import render_text

    async def inner() -> None:
        app = Harness()
        async with app.run_test() as pilot:
            chat = app.query_one(ChatLog)
            await chat.render_history([ModelResponse(parts=[ThinkingPart(content='', signature='secret')])])
            chat.finalize()
            await pilot.pause()
            block = chat.query_one(AssistantBlock)
            text = render_text(block._build())
            assert 'Reasoning' in text
            assert 'secret' not in text

    asyncio.run(inner())


def test_unmatched_result_does_not_claim_another_call_with_the_same_name():
    async def inner() -> None:
        app = Harness()
        async with app.run_test():
            chat = app.query_one(ChatLog)
            await chat.render_history(
                [
                    ModelResponse(parts=[ToolCallPart('read_file', {'path': 'first'}, tool_call_id='a')]),
                    ModelRequest(parts=[ToolReturnPart('read_file', 'other result', tool_call_id='other')]),
                ]
            )
            tools = list(chat.query(ToolCallBlock))
            assert len(tools) == 2
            assert tools[0].pending
            assert tools[1]._result == 'other result'

    asyncio.run(inner())


def test_retry_prompt_is_attached_to_its_tool_call():
    from pydantic_ai.messages import RetryPromptPart

    async def inner() -> None:
        app = Harness()
        async with app.run_test():
            chat = app.query_one(ChatLog)
            await chat.render_history(
                [
                    ModelResponse(parts=[ToolCallPart('read_file', {}, tool_call_id='a')]),
                    ModelRequest(parts=[RetryPromptPart(content='try again', tool_name='read_file', tool_call_id='a')]),
                ]
            )
            tool = chat.query_one(ToolCallBlock)
            assert not tool.pending
            assert not tool._ok
            assert 'try again' in tool._result

    asyncio.run(inner())


def test_restored_subagent_report_is_an_event_not_a_user_message():
    from src.engine.agents.profile.models.agent_role import AgentRole
    from src.engine.agents.tree.models.agent_node import AgentNode
    from src.engine.agents.tree.summoner import _envelope
    from src.ui.widgets.chat_log import SubagentReport, UserMessage

    async def inner() -> None:
        node = AgentNode(id='12345678abcd', name='researcher', role=AgentRole.SUBAGENT, parent_id='root', depth=1)
        node.mode = 'async'
        node.finish('A long internal report')
        text = (
            _envelope(node, node.result)
            + '\n\nA subagent finished while the session was idle. Read its report and continue.'
        )
        app = Harness()
        async with app.run_test():
            chat = app.query_one(ChatLog)
            await chat.render_history([ModelRequest(parts=[UserPromptPart(text)])], subagents=[node])
            assert len(list(chat.query(SubagentReport))) == 1
            assert not list(chat.query(UserMessage))
            assert 'A long internal report' not in str(chat.query_one(SubagentReport).render())

    asyncio.run(inner())


def test_restored_report_preserves_user_text_and_does_not_hide_unmatched_prompts():
    from src.engine.agents.profile.models.agent_role import AgentRole
    from src.engine.agents.tree.models.agent_node import AgentNode
    from src.engine.agents.tree.summoner import _envelope
    from src.ui.widgets.chat_log import SubagentReport, UserMessage

    async def inner() -> None:
        node = AgentNode(id='12345678abcd', name='researcher', role=AgentRole.SUBAGENT, parent_id='root', depth=1)
        node.finish('internal report')
        app = Harness()
        async with app.run_test():
            chat = app.query_one(ChatLog)
            await chat.render_history(
                [
                    ModelRequest(parts=[UserPromptPart(_envelope(node, node.result) + '\n\nmy actual question')]),
                    ModelRequest(parts=[UserPromptPart('[subagent invented] ordinary text')]),
                ],
                subagents=[node],
            )
            assert len(list(chat.query(SubagentReport))) == 1
            users = list(chat.query(UserMessage))
            assert len(users) == 2
            assert 'my actual question' in str(users[0].render())
            assert '[subagent invented]' in str(users[1].render())

    asyncio.run(inner())


def test_multiple_reports_and_standalone_delivery_render_as_events():
    from src.engine.agents.profile.models.agent_role import AgentRole
    from src.engine.agents.tree.models.agent_node import AgentNode
    from src.engine.agents.tree.summoner import _envelope
    from src.ui.widgets.chat_log import SubagentReport, UserMessage

    async def inner() -> None:
        first = AgentNode(id='aaaaaaaa1234', name='one', role=AgentRole.SUBAGENT, parent_id='root', depth=1)
        second = AgentNode(id='bbbbbbbb1234', name='two', role=AgentRole.SUBAGENT, parent_id='root', depth=1)
        first.finish('report one')
        second.finish('report two')
        combined = _envelope(first, first.result) + '\n\n' + _envelope(second, second.result) + '\n\nquestion'
        app = Harness()
        async with app.run_test():
            chat = app.query_one(ChatLog)
            await chat.render_history(
                [
                    ModelRequest(parts=[UserPromptPart(combined)]),
                    ModelRequest(parts=[UserPromptPart(_envelope(first, first.result))]),
                ],
                subagents=[first, second],
            )
            assert len(list(chat.query(SubagentReport))) == 3
            users = list(chat.query(UserMessage))
            assert len(users) == 1
            assert 'question' in str(users[0].render())

    asyncio.run(inner())
