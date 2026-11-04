"""Sigil entry point: builds the agent, the tools, the session and the UI."""

from __future__ import annotations

from pathlib import Path

from src.config_provider import ConfigProvider
from src.engine.agents.agent import create_agent
from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.context.capability import ContextViewCapability
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.scope import ScopeSink, ScopeTracker
from src.engine.context.strategy.compacting import CompactingStrategy
from src.engine.context.strategy.passthrough import PassthroughStrategy
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.files.tools import register_file_tools
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.tools import register_interaction_tools
from src.engine.mcp.config.loader import load_config
from src.engine.mcp.manager.manager import MCPManager
from src.engine.mcp.tools import register_mcp_tools
from src.engine.roadmap.store import RoadmapStore
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
    """View strategy based on configuration.

    - `windowed` (default): trims reasoning, old tool results and old turns.
    - `compacting`: also collapses old turns into a deterministic summary.
    - `passthrough`: sends the history as is.
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
    # A single skill registry shared by the agent instructions
    # (metadata) and by the tools (load, resources and scripts).
    skill_registry = SkillRegistry()

    # Skills loaded in the session: the `load_skill` tool registers them and the
    # instructions function injects them as a block on every request.
    loaded_skills = LoadedSkills()

    # Tool logging hub: the engine emits, the UI adds its sink on mount.
    tool_logger = ToolLoggerHub()

    # Context: the scope follows the directories the tools touch, and the
    # `workspace` block resolves AGENTS.md/CLAUDE.md over that tree.
    workspace_root = Path.cwd()
    scope = ScopeTracker(workspace_root)
    workspace = WorkspaceInstructions(workspace_root)
    tool_logger.add_sink(ScopeSink(scope))

    meter = ContextMeter(output_reserve=ConfigProvider.CONTEXT_OUTPUT_RESERVE)
    context_capability = ContextViewCapability(strategy=_build_strategy(), meter=meter)

    # Active session: the roadmap and the history hang off it.
    store = SessionStore()
    session = store.load_last()
    if session is None:
        session = store.create_session()

    # Bridge for interactive questions and roadmap state: the engine
    # updates, the UI consumes them. The roadmap is per session: it is born empty with
    # each session and is recovered when loading it.
    question_hub = QuestionHub()
    roadmap_store = RoadmapStore(session_id=session.id)

    # MCP servers declared in .sigil/config.json (and in ~/.sigil/config.json).
    # Config errors do not abort startup: they are shown in the chat.
    config = load_config()
    mcp_manager = MCPManager(config.servers, tool_logger=tool_logger)

    # Retries on transient model failures. The instance is shared
    # with the UI so it announces every retry in the chat.
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
