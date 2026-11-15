"""RollbackRequested message of the terminal."""

from __future__ import annotations

from textual.message import Message


class RollbackRequested(Message):
    """A user message was clicked: which instant, by its seq in the transcript."""

    def __init__(self, seq: int) -> None:
        super().__init__()
        self.seq = seq
