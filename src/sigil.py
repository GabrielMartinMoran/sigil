"""Punto de entrada de Sigil: arma el agente, las tools, la sesión y la UI."""

from __future__ import annotations

from src.config_provider import ConfigProvider
from src.engine.agents.agent import create_agent
from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.mcp import MCPManager, load_config, register_mcp_tools
from src.engine.roadmap import RoadmapStore
from src.engine.sessions.session_store import SessionStore
from src.engine.skills.registry import SkillRegistry
from src.engine.tools.files import register_file_tools
from src.engine.tools.interaction import QuestionHub, register_interaction_tools
from src.engine.tools.roadmap import register_roadmap_tools
from src.engine.tools.tool_manager import ToolManager
from src.engine.tools.tool_registry import ToolLoggerHub
from src.engine.tools.web import register_web_tools
from src.ui.app import SigilApp


def run() -> None:
    # Un único registro de skills compartido por las instrucciones del agente
    # (metadatos) y por las tools (carga, recursos y scripts).
    skill_registry = SkillRegistry()

    # Hub de logging de tools: el engine emite, la UI agrega su sink al montar.
    tool_logger = ToolLoggerHub()

    # Puente para preguntas interactivas y estado del roadmap: el engine
    # actualiza, la UI los consume.
    question_hub = QuestionHub()
    roadmap_store = RoadmapStore()

    # Servidores MCP declarados en .sigil/config.json (y en ~/.sigil/config.json).
    # Los errores de configuración no abortan el arranque: se muestran en el chat.
    config = load_config()
    mcp_manager = MCPManager(config.servers, tool_logger=tool_logger)

    # Reintentos ante fallos transitorios del modelo. La instancia se comparte
    # con la UI para que avise en el chat cada vez que se reintenta.
    model_retry = RetryTransientModelErrors(max_attempts=ConfigProvider.MODEL_MAX_ATTEMPTS)

    agent = create_agent(skill_registry, toolsets=mcp_manager.toolsets, model_retry=model_retry)

    tool_manager = ToolManager(skill_registry=skill_registry, tool_logger=tool_logger)
    tool_manager.register_tools(agent)
    register_mcp_tools(agent, mcp_manager, logger=tool_logger)
    register_web_tools(agent, logger=tool_logger)
    register_file_tools(agent, logger=tool_logger)
    register_interaction_tools(agent, question_hub, logger=tool_logger)
    register_roadmap_tools(agent, roadmap_store, logger=tool_logger)

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
        question_hub=question_hub,
        roadmap_store=roadmap_store,
        model_retry=model_retry,
    ).run()
