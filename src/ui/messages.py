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
