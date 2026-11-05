"""A cancelled turn leaves a history the next turn can use.

Cancelling inside a tool interrupts the run in the middle of a tool batch: the
calls that already answered keep their result, the rest never will. pydantic-ai
refuses a new prompt on top of a call that has no result, so the history that
gets saved closes the gaps. Without that, one `Esc` would break the session for
every message after it.
"""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.tools import register_interaction_tools
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.question_panel import QuestionPanel


def _make_app(tmp: str, agent: Agent, hub: QuestionHub) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(agent=agent, store=store, session=store.create_session(), question_hub=hub)


async def _wait_for_panel(app, pilot, timeout: int = 60) -> None:
    for _ in range(timeout):
        await pilot.pause()
        if app.query_one(QuestionPanel).display:
            return


async def _wait_idle(app, pilot, timeout: int = 60) -> None:
    for _ in range(timeout):
        await pilot.pause()
        if not app._turn_active:
            return


def _returned_tools(app: SigilApp) -> set[str]:
    return {
        part.tool_name
        for message in app._session.messages
        for part in message.parts
        if type(part).__name__ == 'ToolReturnPart'
    }


def test_a_dangling_tool_call_is_closed_out_so_the_session_survives():

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            agent = Agent(TestModel(call_tools=['ask_user', 'other']), instructions='x')
            register_interaction_tools(agent, hub)

            @agent.tool_plain
            async def other() -> str:
                # Still running when the question gets cancelled: its call is
                # the one left without a result.
                await asyncio.sleep(0.5)
                return 'ok'

            app = _make_app(tmp, agent, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.run_turn('decide for me')
                await _wait_for_panel(app, pilot)

                await pilot.press('escape')
                await _wait_idle(app, pilot)

                # Every call of the batch has a result, including the one that
                # was cut off mid-flight.
                assert _returned_tools(app) == {'ask_user', 'other'}

                # And the next message starts a new turn instead of failing on an
                # unprocessed call.
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)
                texts = [str(widget.render()) for widget in app.query_one('#chat').children]
                assert not any('Error' in text for text in texts)
                assert any('hi' in text for text in texts)

    asyncio.run(inner())
