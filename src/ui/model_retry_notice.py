"""ModelRetryNotice message of the terminal."""

from __future__ import annotations

from textual.message import Message

from src.core.agents.retry import RetryNotice


class ModelRetryNotice(Message):
    """Notice that a transient model failure triggers a retry."""

    def __init__(self, notice: RetryNotice) -> None:
        super().__init__()
        self.notice = notice
