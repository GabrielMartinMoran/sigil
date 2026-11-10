"""Shared helpers for the UI tests.

`render_text` exists because the widgets paint with Rich renderables, and comparing
against `str(renderable)` only ever prints a repr. Tests want the text a reader would
see, which is what a console with no color captures.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.console import Console
from textual.app import App, ComposeResult
from textual.pilot import Pilot

from src.ui.widgets.chat_log import ChatLog

if TYPE_CHECKING:
    from src.ui.app import SigilApp


class ChatApp(App[None]):
    """Host real transcript lifecycle tests without unrelated application services.

    Layout, app theme propagation, commands, and session persistence still need
    SigilApp. This host only exercises ChatLog and its mounted children.
    """

    def compose(self) -> ComposeResult:
        yield ChatLog(id='chat')


async def fill_chat(chat: ChatLog, *, lines: int = 100) -> None:
    """Seed scrolling tests with one long message, not a widget per line.

    Multi-message mounting and streaming remain covered in test_ui_app.py.
    Callers still settle real layout before inspecting scroll positions.
    """
    await chat.add_user('\n'.join(f'message {index}' for index in range(lines)))


async def wait_for_turn(app: SigilApp, *, timeout: float = 5) -> None:
    """Await actual turn completion, without CPU-idle polling or other workers.

    Call after start_turn or after the UI has handled submission. Layout-sensitive
    assertions still need a Pilot barrier after this worker-level synchronization.
    """
    async with asyncio.timeout(timeout):
        await asyncio.gather(*(worker.wait() for worker in app.workers if worker.name == 'run_turn'))
    assert not app._turn_active, 'Turn still active after its workers completed'


async def wait_until(pilot: Pilot, condition: Callable[[], bool], *, timeout: float = 5) -> None:
    """Drain UI events until the expected state is observable, with a deadline.

    Keep Pilot's idle wait so polling also gives loader threads time to run.
    """
    async with asyncio.timeout(timeout):
        while True:
            await pilot.pause()
            if condition():
                return


def render_text(renderable: Any, *, width: int = 200) -> str:
    """Plain text of a Rich renderable, colors dropped.

    The console has to be wide: a narrow one wraps the lines and the assertion then
    fails on a line break instead of on the content.
    """
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()
