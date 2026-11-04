"""Tool event sink that writes into the UI chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.message import Message

from src.engine.tooling.events import ToolCallEvent

if TYPE_CHECKING:
    from src.ui.app import SigilApp


class ToolLogMessage(Message):
    """Textual message to update the chat from the tool logger."""

    def __init__(self, event: ToolCallEvent) -> None:
        super().__init__()
        self.event = event


class ChatToolLogSink:
    """Translates ``ToolLoggerHub`` events into app messages.

    The sink runs in the tool's context (possibly sync and on another
    task/thread), so it does not touch widgets directly: it queues a
    ``ToolLogMessage`` and the app processes it in its message pump.
    """

    def __init__(self, app: SigilApp) -> None:
        self._app = app

    def __call__(self, event: ToolCallEvent) -> None:
        self._app.post_message(ToolLogMessage(event))
