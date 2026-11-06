"""Internal messages of the Sigil UI."""

from __future__ import annotations

from textual.message import Message

from src.engine.agents.retry import RetryNotice
from src.engine.agents.tree.models.agent_node import AgentNode


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


class NodeOpened(Message):
    """A subagent was clicked in the chat.

    The block knows which execution it belongs to, and the app knows how to open
    one; a message between them is the shortest path that does not teach the chat
    about the app.
    """

    def __init__(self, node_ref: str) -> None:
        super().__init__()
        self.node_ref = node_ref


class TreeChanged(Message):
    """A node of the session tree was born or moved on.

    Posted by the summoner's listener, which runs inside an agent task: it only
    says *what* changed, and the app decides what that means for what is on screen.
    """

    def __init__(self, node: AgentNode) -> None:
        super().__init__()
        self.node = node


class RollbackRequested(Message):
    """A user message was clicked: which instant, by its seq in the transcript."""

    def __init__(self, seq: int) -> None:
        super().__init__()
        self.seq = seq
