"""The UI boundary for accepting a freshly loaded conversation."""

from __future__ import annotations

from typing import Protocol, cast

from textual.message import Message

from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.sessions.session_store import Session, SessionStore
from src.ui.widgets.chat_log import ChatLog


class SessionView(Protocol):
    _store: SessionStore
    _session: Session

    def bind_session(self, session: Session) -> None: ...

    def _restore_session_model(self, session: Session) -> None: ...

    def tree_nodes(self) -> list[AgentNode]: ...

    def post_message(self, message: Message) -> bool: ...


def session_view(app: object) -> SessionView:
    """Expose the assembled app's contract to its independently typed mixins."""
    return cast(SessionView, app)


def bind_refreshed_session(view: SessionView, session: Session) -> None:
    view.bind_session(session)
    view._restore_session_model(session)


async def render_refreshed_session(view: SessionView, session: Session, chat: ChatLog) -> None:
    bind_refreshed_session(view, session)
    await chat.clear()
    await chat.render_history(session.messages, subagents=view.tree_nodes())
    chat.finalize()
