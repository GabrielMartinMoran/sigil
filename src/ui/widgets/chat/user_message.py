"""The block that shows what the user typed."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

from src.ui.messages.rollback_requested import RollbackRequested


class UserMessage(Static):
    """One user message, clickable when its place in the conversation is known.

    A click does not decide anything: it opens the screen that asks what to do with
    the instant *before* this message — roll back to it, or fork from it. Deciding
    here would be deciding for the user on the one gesture that can destroy a turn.

    ``seq`` is the position of the prompt in the root transcript, which is what maps
    it to its turn. It is ``None`` for a message that has not reached a checkpoint yet,
    and the click is honest about that: it does nothing instead of guessing.
    """

    def __init__(self, renderable: Any, *, seq: int | None = None, **kwargs: Any) -> None:
        super().__init__(renderable, **kwargs)
        self.seq = seq

    def on_click(self) -> None:
        if self.seq is None:
            return
        self.post_message(RollbackRequested(self.seq))
