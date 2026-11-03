"""Punto de entrada de Sigil: arma el agente, las tools, la sesión y la UI."""

from __future__ import annotations

from src.engine.agents.agent import create_agent
from src.engine.sessions.session_store import SessionStore
from src.engine.tools.tool_manager import ToolManager
from src.ui.app import SigilApp


def run() -> None:
    agent = create_agent()

    tool_manager = ToolManager()
    tool_manager.register_tools(agent)

    store = SessionStore()
    session = store.load_last()
    if session is None:
        session = store.create_session()

    SigilApp(agent=agent, store=store, session=session).run()
