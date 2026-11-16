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
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from src.core.sessions.recover_history import recover_history
from src.core.sessions.session_store import SessionStore
from src.ui.sigil_app import SigilApp
from src.ui.widgets.chat.assistant_block import AssistantBlock
from src.ui.widgets.chat.chat_log import ChatLog


def _cut_mid_tool_call() -> list:
    """What the checkpoint wrote before the process died: a call with no result."""
    return [
        ModelRequest(parts=[UserPromptPart('do it')]),
        ModelResponse(parts=[ToolCallPart('run', '{}', 'c1')]),
    ]


def _returned_ids(messages: list) -> set[str]:
    return {part.tool_call_id for message in messages for part in message.parts if isinstance(part, ToolReturnPart)}


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


def test_a_session_saved_after_a_crash_starts_a_new_turn() -> None:
    """Regression: the `unprocessed tool calls` error only showed up on the next prompt."""

    async def inner() -> None:
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


def test_a_turn_that_dies_on_an_exception_saves_a_usable_history() -> None:
    """The other half of the fix: the damage never reaches disk at all.

    A tool that raises mid-batch leaves the run cancelled with a response whose
    calls have no answer. The turn's final save repairs it, so the next process to
    open the session does not find a history a provider rejects.
    """

    async def inner() -> None:
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
