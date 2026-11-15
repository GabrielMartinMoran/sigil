"""A session a dead process left mid-turn can be used again.

A turn is checkpointed after every stream event, so the file on disk can end
between a model response and the results of its tool calls: the process was
killed, the terminal closed, the machine slept. pydantic-ai refuses a new prompt
on top of a response whose calls have no answer, and the refusal is what the user
sees — a session broken forever by a crash.

`recover_history` closes that gap before the history reaches the agent, and the
app reports it once in the chat instead of surfacing a framework error.
"""

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from src.core.sessions.recovery import recover_history


def _cut_mid_tool_call() -> list:
    """What the checkpoint wrote before the process died: a call with no result."""
    return [
        ModelRequest(parts=[UserPromptPart('do it')]),
        ModelResponse(parts=[ToolCallPart('run', '{}', 'c1')]),
    ]


def _returned_ids(messages: list) -> set[str]:
    return {part.tool_call_id for message in messages for part in message.parts if isinstance(part, ToolReturnPart)}


# --- the recovery itself ------------------------------------------------------


def test_a_finished_history_is_left_alone() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart('hello')]),
        ModelResponse(parts=[TextPart('hey')]),
    ]

    recovered = recover_history(messages)

    assert recovered.changed is False
    assert recovered.messages == messages
    assert recovered.notice == ''


def test_an_unanswered_call_is_closed_out() -> None:
    recovered = recover_history(_cut_mid_tool_call())

    assert recovered.changed is True
    assert recovered.closed_calls == 1
    assert recovered.unfinished_responses == 0
    assert _returned_ids(recovered.messages) == {'c1'}
    assert '1 tool call' in recovered.notice


def test_the_recovered_history_accepts_a_new_prompt() -> None:
    """The point of the repair: the next turn runs instead of raising."""

    async def inner() -> None:
        agent = Agent(TestModel(custom_output_text='ok'), instructions='x')

        @agent.tool_plain
        def run() -> str:
            return 'done'

        recovered = recover_history(_cut_mid_tool_call())
        result = await agent.run('next message', message_history=recovered.messages)
        assert result.output == 'ok'

    asyncio.run(inner())


def test_a_paused_response_is_closed_out_too() -> None:
    """A provider that paused mid-turn left a tail nothing will ever resume."""
    messages = [
        ModelRequest(parts=[UserPromptPart('hello')]),
        ModelResponse(parts=[ToolCallPart('run', '{}', 'c1')], state='suspended'),
    ]

    recovered = recover_history(messages)

    assert recovered.changed is True
    assert recovered.unfinished_responses == 1
    assert recovered.closed_calls == 1
    assert recovered.messages[-1].state != 'suspended'
    assert _returned_ids(recovered.messages) == {'c1'}


def test_recovering_twice_changes_nothing_the_second_time() -> None:
    once = recover_history(_cut_mid_tool_call())
    twice = recover_history(once.messages)

    assert twice.changed is False
    assert twice.messages == once.messages


def test_an_empty_history_is_not_a_failure() -> None:
    assert recover_history([]).messages == []


def test_a_history_recovered_mid_turn_keeps_its_answered_calls() -> None:
    """Only the dangling call is closed; the results the dead turn did collect stay."""
    messages = [
        ModelRequest(parts=[UserPromptPart('do both')]),
        ModelResponse(parts=[ToolCallPart('read_file', '{}', 'c1'), ToolCallPart('run', '{}', 'c2')]),
        ModelRequest(parts=[ToolReturnPart('read_file', 'contents', 'c1')]),
    ]

    recovered = recover_history(messages)

    assert recovered.closed_calls == 1
    assert _returned_ids(recovered.messages) == {'c1', 'c2'}
    # The real result is the one from the tool, not a synthesized stand-in.
    original = next(part for part in recovered.messages[2].parts if part.tool_call_id == 'c1')
    assert original.content == 'contents'


def test_a_dangling_call_from_an_earlier_step_is_left_alone() -> None:
    """Only the frontier can make the next prompt fail.

    A call with no result in the middle of the conversation is not a turn that died:
    it is a batch a steer, a retry or a lost write split in two. `Agent.run` accepts
    it — only the *last* response's unprocessed calls are refused — and pydantic-ai
    closes it in memory before every request. Repairing it here invented tool output
    that was never produced, wrote it to disk and then reported a crash that never
    happened.
    """
    messages = [
        ModelRequest(parts=[UserPromptPart('first')]),
        ModelResponse(parts=[ToolCallPart('run', '{}', 'c1'), ToolCallPart('run', '{}', 'c2')]),
        ModelRequest(parts=[ToolReturnPart('run', 'ok', 'c1')]),
        ModelRequest(parts=[UserPromptPart('second')]),
        ModelResponse(parts=[TextPart('answered')]),
    ]

    recovered = recover_history(messages)

    assert recovered.changed is False
    assert recovered.messages is messages
    assert recovered.closed_calls == 0
    assert recovered.notice == ''
