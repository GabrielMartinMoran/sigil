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
import tempfile
from pathlib import Path

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

from src.engine.sessions.recovery import recover_history
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import AssistantBlock, ChatLog


def _cut_mid_tool_call() -> list:
    """What the checkpoint wrote before the process died: a call with no result."""
    return [
        ModelRequest(parts=[UserPromptPart('do it')]),
        ModelResponse(parts=[ToolCallPart('run', '{}', 'c1')]),
    ]


def _returned_ids(messages: list) -> set[str]:
    return {part.tool_call_id for message in messages for part in message.parts if isinstance(part, ToolReturnPart)}


# --- the recovery itself ------------------------------------------------------


def test_a_finished_history_is_left_alone():
    messages = [
        ModelRequest(parts=[UserPromptPart('hello')]),
        ModelResponse(parts=[TextPart('hey')]),
    ]

    recovered = recover_history(messages)

    assert recovered.changed is False
    assert recovered.messages == messages
    assert recovered.notice == ''


def test_an_unanswered_call_is_closed_out():
    recovered = recover_history(_cut_mid_tool_call())

    assert recovered.changed is True
    assert recovered.closed_calls == 1
    assert recovered.unfinished_responses == 0
    assert _returned_ids(recovered.messages) == {'c1'}
    assert '1 tool call' in recovered.notice


def test_the_recovered_history_accepts_a_new_prompt():
    """The point of the repair: the next turn runs instead of raising."""

    async def inner():
        agent = Agent(TestModel(custom_output_text='ok'), instructions='x')

        @agent.tool_plain
        def run() -> str:
            return 'done'

        recovered = recover_history(_cut_mid_tool_call())
        result = await agent.run('next message', message_history=recovered.messages)
        assert result.output == 'ok'

    asyncio.run(inner())


def test_a_paused_response_is_closed_out_too():
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


def test_recovering_twice_changes_nothing_the_second_time():
    once = recover_history(_cut_mid_tool_call())
    twice = recover_history(once.messages)

    assert twice.changed is False
    assert twice.messages == once.messages


def test_an_empty_history_is_not_a_failure():
    assert recover_history([]).messages == []


def test_a_history_recovered_mid_turn_keeps_its_answered_calls():
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


def test_a_dangling_call_from_an_earlier_step_is_left_alone():
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


# --- the app ------------------------------------------------------------------


def _chat_texts(app: SigilApp) -> list[str]:
    texts: list[str] = []
    for widget in app.query_one(ChatLog).children:
        if isinstance(widget, AssistantBlock):
            texts.append(widget._text)
            continue
        rendered = widget.render()
        plain = getattr(rendered, 'plain', None)
        texts.append(plain if isinstance(plain, str) else str(rendered))
    return texts


async def _wait_idle(app, pilot, attempts: int = 80) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if not app._turn_active:
            return


def test_a_session_saved_after_a_crash_starts_a_new_turn():
    """Regression: the `unprocessed tool calls` error only showed up on the next prompt."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='answered'), instructions='x')

            @agent.tool_plain
            def run() -> str:
                return 'done'

            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            session.messages = _cut_mid_tool_call()
            store.save(session)

            app = SigilApp(agent=agent, store=store, session=session)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press('a', 'g', 'a', 'i', 'n')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                texts = _chat_texts(app)
                assert not any('Error' in text for text in texts), texts
                assert any('answered' in text for text in texts), texts
                # The recovery is reported once, not silently swallowed.
                assert any('Recovered the session' in text for text in texts), texts
                # And what the dead turn left behind is closed out on disk.
                assert _returned_ids(app._session.messages) == {'c1'}
                saved = store.load(session.id)
                assert saved is not None
                assert _returned_ids(saved.messages) == {'c1'}

    asyncio.run(inner())


def test_a_turn_that_dies_on_an_exception_saves_a_usable_history():
    """The other half of the fix: the damage never reaches disk at all.

    A tool that raises mid-batch leaves the run cancelled with a response whose
    calls have no answer. The turn's final save repairs it, so the next process to
    open the session does not find a history a provider rejects.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(call_tools=['hold']), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                raise RuntimeError('the tool died')

            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            app = SigilApp(agent=agent, store=store, session=session)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                await _wait_idle(app, pilot)

                saved = store.load(session.id)
                assert saved is not None
                # The call got a closed-out result instead of being left waiting.
                returned = [p for m in saved.messages for p in m.parts if isinstance(p, ToolReturnPart)]
                assert len(returned) == 1
                assert returned[0].tool_name == 'hold'
                assert getattr(returned[0], 'outcome', 'success') != 'success'
                # And nothing is left for the next process to repair.
                assert recover_history(saved.messages).changed is False

    asyncio.run(inner())
