"""SessionView message of the terminal."""

from __future__ import annotations

from typing import Protocol

from textual.message import Message

from src.core.agents.tree.models.agent_node import AgentNode
from src.core.sessions.session_store import Session, SessionStore


class SessionView(Protocol):
    _store: SessionStore
    _session: Session

    def bind_session(self, session: Session) -> None: ...

    def _restore_session_model(self, session: Session) -> None: ...

    def tree_nodes(self) -> list[AgentNode]: ...

    def post_message(self, message: Message) -> bool: ...
