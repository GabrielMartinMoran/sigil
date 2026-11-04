"""Punto de entrada de Sigil: arma el agente, las tools, la sesión y la UI."""

from __future__ import annotations

from pathlib import Path

from src.config_provider import ConfigProvider
from src.engine.agents.agent import create_agent
from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.context import (
    CompactingStrategy,
    ContextMeter,
    ContextViewCapability,
    PassthroughStrategy,
    ScopeSink,
    ScopeTracker,
    WindowedStrategy,
    WorkspaceInstructions,
)
from src.engine.files.tools import register_file_tools
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.tools import register_interaction_tools
from src.engine.mcp import MCPManager, load_config, register_mcp_tools
from src.engine.roadmap import RoadmapStore
from src.engine.roadmap.tools import register_roadmap_tools
from src.engine.sessions.session_store import SessionStore
from src.engine.shell.tools import register_shell_tools
from src.engine.skills.loaded import LoadedSkills
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.tools import register_skill_tools
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.web.tools import register_web_tools
from src.ui.app import SigilApp


def _build_strategy():
    """Estrategia de vista según configuración.

    - `windowed` (default): recorta razonamiento, tool results viejos y turnos viejos.
    - `compacting`: además colapsa los turnos viejos en un resumen determinístico.
    - `passthrough`: manda el historial tal cual.
    """
    if ConfigProvider.CONTEXT_STRATEGY == 'passthrough':
        return PassthroughStrategy()
    if ConfigProvider.CONTEXT_STRATEGY == 'compacting':
        return CompactingStrategy(
            keep_recent_turns=ConfigProvider.CONTEXT_KEEP_RECENT_TURNS,
            max_tool_result_chars=ConfigProvider.CONTEXT_MAX_TOOL_RESULT_CHARS,
            keep_thinking=ConfigProvider.CONTEXT_KEEP_THINKING,
        )
    return WindowedStrategy(
        keep_recent_turns=ConfigProvider.CONTEXT_KEEP_RECENT_TURNS,
        max_tool_result_chars=ConfigProvider.CONTEXT_MAX_TOOL_RESULT_CHARS,
        keep_thinking=ConfigProvider.CONTEXT_KEEP_THINKING,
    )


def run() -> None:
    # Un único registro de skills compartido por las instrucciones del agente
    # (metadatos) y por las tools (carga, recursos y scripts).
    skill_registry = SkillRegistry()

    # Skills cargados en la sesión: la tool `load_skill` los registra y la
    # función de instrucciones los inyecta como bloque en cada request.
    loaded_skills = LoadedSkills()

    # Hub de logging de tools: el engine emite, la UI agrega su sink al montar.
    tool_logger = ToolLoggerHub()

    # Contexto: el scope sigue los directorios que tocan las tools, y el bloque
    # `workspace` resuelve AGENTS.md/CLAUDE.md sobre ese árbol.
    workspace_root = Path.cwd()
    scope = ScopeTracker(workspace_root)
    workspace = WorkspaceInstructions(workspace_root)
    tool_logger.add_sink(ScopeSink(scope))

    meter = ContextMeter(output_reserve=ConfigProvider.CONTEXT_OUTPUT_RESERVE)
    context_capability = ContextViewCapability(strategy=_build_strategy(), meter=meter)

    # Sesión activa: el roadmap y el historial cuelgan de ella.
    store = SessionStore()
    session = store.load_last()
    if session is None:
        session = store.create_session()

    # Puente para preguntas interactivas y estado del roadmap: el engine
    # actualiza, la UI los consume. El roadmap es por sesión: nace vacío con
    # cada sesión y se recupera al cargarla.
    question_hub = QuestionHub()
    roadmap_store = RoadmapStore(session_id=session.id)

    # Servidores MCP declarados en .sigil/config.json (y en ~/.sigil/config.json).
    # Los errores de configuración no abortan el arranque: se muestran en el chat.
    config = load_config()
    mcp_manager = MCPManager(config.servers, tool_logger=tool_logger)

    # Reintentos ante fallos transitorios del modelo. La instancia se comparte
    # con la UI para que avise en el chat cada vez que se reintenta.
    model_retry = RetryTransientModelErrors(max_attempts=ConfigProvider.MODEL_MAX_ATTEMPTS)

    agent = create_agent(
        skill_registry,
        toolsets=mcp_manager.toolsets,
        model_retry=model_retry,
        workspace=workspace,
        scope=scope,
        loaded_skills=loaded_skills,
        extra_capabilities=[context_capability],
    )

    register_shell_tools(agent, logger=tool_logger)
    register_skill_tools(agent, skill_registry, logger=tool_logger, loaded=loaded_skills)
    register_mcp_tools(agent, mcp_manager, logger=tool_logger)
    register_web_tools(agent, logger=tool_logger)
    register_file_tools(agent, logger=tool_logger)
    register_interaction_tools(agent, question_hub, logger=tool_logger)
    register_roadmap_tools(agent, roadmap_store, logger=tool_logger)

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
        meter=meter,
        context_capability=context_capability,
        scope=scope,
        workspace=workspace,
        loaded_skills=loaded_skills,
    ).run()
