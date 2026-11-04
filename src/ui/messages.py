"""Internal messages of the Sigil UI."""

from __future__ import annotations

from textual.message import Message

from src.engine.agents.retry import RetryNotice


class RoadmapChanged(Message):
    """The roadmap store changed and the panel has to repaint."""


class ModelRetryNotice(Message):
    """Notice that a transient model failure triggers a retry."""

    def __init__(self, notice: RetryNotice) -> None:
        super().__init__()
        self.notice = notice


class ChatNotice(Message):
    """A line that must be added to the chat from a synchronous context.

    Screens and mixin helpers run inside a worker or a callback where the chat
    widget is not awaitable; posting a message keeps the write on the app's
    message loop, in order, without blocking.
    """

    def __init__(self, text: str, *, style: str = 'white') -> None:
        super().__init__()
        self.text = text
        self.style = style
