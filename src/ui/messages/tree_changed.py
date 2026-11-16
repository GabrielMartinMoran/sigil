"""TreeChanged message of the terminal."""

from __future__ import annotations

from textual.message import Message

from src.core.agents.tree.models.agent_node import AgentNode


class TreeChanged(Message):
    """A node of the session tree was born or moved on.

    Posted by the summoner's listener, which runs inside an agent task: it only
    says *what* changed, and the app decides what that means for what is on screen.
    """

    def __init__(self, node: AgentNode) -> None:
        super().__init__()
        self.node = node
