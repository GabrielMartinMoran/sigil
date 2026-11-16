"""ToolLogMessage message of the terminal."""

from __future__ import annotations

from textual.message import Message

from src.core.tooling.tool_call_event import ToolCallEvent


class ToolLogMessage(Message):
    """Textual message to update the chat from the tool logger."""

    def __init__(self, event: ToolCallEvent) -> None:
        super().__init__()
        self.event = event
