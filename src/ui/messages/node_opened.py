"""NodeOpened message of the terminal."""

from __future__ import annotations

from textual.message import Message


class NodeOpened(Message):
    """A subagent was clicked in the chat.

    The block knows which execution it belongs to, and the app knows how to open
    one; a message between them is the shortest path that does not teach the chat
    about the app.
    """

    def __init__(self, node_ref: str) -> None:
        super().__init__()
        self.node_ref = node_ref
