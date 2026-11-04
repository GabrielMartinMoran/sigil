"""Punto de entrada de Sigil: arma el agente, las tools, la sesión y la UI."""

from __future__ import annotations

from src.engine.agents.agent import create_agent
from src.engine.mcp import MCPManager, load_config, register_mcp_tools
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

    # Servidores MCP declarados en .sigil/config.json (y en ~/.sigil/config.json).
    # Los errores de configuración no abortan el arranque: se muestran en el chat.
    config = load_config()
    mcp_manager = MCPManager(config.servers, tool_logger=tool_logger)

    agent = create_agent(skill_registry, toolsets=mcp_manager.toolsets)

    tool_manager = ToolManager(skill_registry=skill_registry, tool_logger=tool_logger)
    tool_manager.register_tools(agent)
    register_mcp_tools(agent, mcp_manager, logger=tool_logger)

    store = SessionStore()
    session = store.load_last()
    if session is None:
        session = store.create_session()

    SigilApp(
        agent=agent,
        store=store,
        session=session,
        tool_logger=tool_logger,
        mcp_manager=mcp_manager,
        startup_notices=(*config.errors, *mcp_manager.errors),
    ).run()
