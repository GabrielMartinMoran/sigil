"""ChatToolLogSink message of the terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.tooling.tool_call_event import ToolCallEvent
from src.ui.messages.tool_log_message import ToolLogMessage

if TYPE_CHECKING:
    from src.ui.sigil_app import SigilApp


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
