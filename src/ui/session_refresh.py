"""The UI boundary for accepting a freshly loaded conversation."""

from __future__ import annotations

from typing import cast

from src.core.sessions.session import Session
from src.ui.session_view import SessionView
from src.ui.widgets.chat_log import ChatLog


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
