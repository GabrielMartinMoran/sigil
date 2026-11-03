"""Punto de entrada de Sigil: arma el agente, las tools, la sesión y la UI."""

from __future__ import annotations

from src.engine.agents.agent import create_agent
from src.engine.sessions.session_store import SessionStore
from src.engine.skills.registry import SkillRegistry
from src.engine.tools.tool_manager import ToolManager
from src.engine.tools.tool_registry import ToolLoggerHub
from src.ui.app import SigilApp


def run() -> None:
    # Un único registro de skills compartido por las instrucciones del agente
    # (metadatos) y por las tools (carga, recursos y scripts).
    skill_registry = SkillRegistry()

    # Hub de logging de tools: el engine emite, la UI agrega su sink al montar.
    tool_logger = ToolLoggerHub()

    agent = create_agent(skill_registry)

    tool_manager = ToolManager(skill_registry=skill_registry, tool_logger=tool_logger)
    tool_manager.register_tools(agent)

    store = SessionStore()
    session = store.load_last()
    if session is None:
        session = store.create_session()

    SigilApp(agent=agent, store=store, session=session, tool_logger=tool_logger).run()
