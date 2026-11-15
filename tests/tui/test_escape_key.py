"""The real key, not a synthesised one: a lone ESC byte becomes `escape`.

A terminal sends a single 0x1B for the `Esc` key, and the same byte starts every
escape sequence (arrow keys, F-keys, the Kitty protocol). Textual resolves the
ambiguity with a timeout (`ESCDELAY`, 100 ms by default): it waits that long for a
continuation and, finding none, emits `escape`.

That delay is why the abort window is measured in seconds and not in keystrokes,
and this test pins both ends of it: the byte really produces the event, and the
event really reaches the app's handler.
"""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual import events
from textual._xterm_parser import XTermParser

from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.status_bar import StatusBar


def _parse_escape_bytes() -> list[object]:
    """Feeds a lone ESC to the parser, the way the terminal does."""
    parser = XTermParser(debug=False)
    # Nothing yet: the parser is waiting for a possible continuation, which is
    # what keeps `Esc` from being confused with the start of an arrow key.
    assert not list(parser.feed('\x1b'))
    # The delay expires and the byte is resolved.
    return list(parser.feed(''))


def test_a_lone_esc_byte_becomes_the_escape_key():
    keys = [event for event in _parse_escape_bytes() if isinstance(event, events.Key)]
    assert len(keys) == 1
    assert keys[0].key == 'escape'
    # The ambiguous byte is reported as such: this is the key the prompt and the
    # app both look for.
    assert keys[0].character == '\x1b'


def test_a_real_esc_byte_reaches_the_abort_handler():
    """The parser's output goes straight into the app and asks to stop."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()
            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(agent=agent, store=store, session=store.create_session())
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(60):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                for event in _parse_escape_bytes():
                    if isinstance(event, events.Key):
                        app.post_message(event)
                await pilot.pause()
                assert 'again' in str(app.query_one(StatusBar).render())

                release.set()
                app.request_abort()
                app.abort_turn()
                for _ in range(60):
                    await pilot.pause()
                    if not app._turn_active:
                        break

    asyncio.run(inner())
