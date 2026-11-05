"""Escape is the abort, and it has to survive where the focus actually is.

`Esc` is read by the prompt while the prompt has the focus, and by the app
binding when something else does. Two paths, one behaviour: this file walks both
so a change in one does not silently break the other.
"""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.status_bar import StatusBar


def _app(tmp: str, agent: Agent) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(agent=agent, store=store, session=store.create_session())


async def _wait_idle(app, pilot, attempts: int = 80) -> bool:
    for _ in range(attempts):
        await pilot.pause()
        if not app._turn_active:
            return True
    return False


def _status(app: SigilApp) -> str:
    return str(app.query_one(StatusBar).render())


def _agent_with_a_blocking_tool(release: asyncio.Event) -> Agent:
    agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

    @agent.tool_plain
    async def hold() -> str:
        await release.wait()
        return 'ok'

    return agent


def test_escape_from_the_focused_prompt_asks_then_stops():
    """The path that matters: typing has the focus, so the prompt reads the key."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()
            app = _app(tmp, _agent_with_a_blocking_tool(release))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(60):
                    await pilot.pause()
                    if app._current_run is not None:
                        break
                assert app.focused is app.query_one(PromptInput)

                await pilot.press('escape')
                await pilot.pause()
                assert app._turn_active is True
                assert 'again' in _status(app)

                await pilot.press('escape')
                assert await _wait_idle(app, pilot)
                assert any('Turn cancelled.' in str(w.render()) for w in app.query_one(ChatLog).children)
                assert 'again' not in _status(app)

    asyncio.run(inner())


def test_escape_works_when_the_focus_is_not_in_the_prompt():
    """With the keys elsewhere, the app binding is the one that answers."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()
            app = _app(tmp, _agent_with_a_blocking_tool(release))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(60):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                # The chat takes the focus: the prompt no longer sees the key.
                app.query_one(ChatLog).focus()
                await pilot.pause()
                assert app.focused is not app.query_one(PromptInput)

                await pilot.press('escape')
                await pilot.pause()
                assert app._turn_active is True
                assert 'again' in _status(app)

                await pilot.press('escape')
                assert await _wait_idle(app, pilot)

    asyncio.run(inner())


def test_escape_without_a_turn_does_nothing_visible():
    """No turn, no question: `Esc` stays out of the way."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()
            app = _app(tmp, _agent_with_a_blocking_tool(release))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press('escape')
                await pilot.pause()
                assert 'again' not in _status(app)
                assert app._abort_deadline is None

    asyncio.run(inner())


def test_any_other_key_cancels_the_abort_even_with_the_focus_elsewhere():
    """The rule is app wide, not prompt wide: typing anywhere calls it off."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()
            app = _app(tmp, _agent_with_a_blocking_tool(release))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(60):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                app.query_one(ChatLog).focus()
                await pilot.pause()
                await pilot.press('escape')
                await pilot.pause()
                assert 'again' in _status(app)

                await pilot.press('j')  # scrolling the chat
                await pilot.pause()
                assert 'again' not in _status(app)
                assert app._abort_deadline is None
                assert app._turn_active is True

                release.set()
                assert await _wait_idle(app, pilot)

    asyncio.run(inner())
