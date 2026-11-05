"""Sigil entry point: builds the agent, the tools, the session and the UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.engine.agents.agent import create_agent
from src.engine.agents.model_features import IMAGE_INPUT, ModelFeatureGate
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.attachments.tools import register_image_tools
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
from src.engine.mcp.deferred import MCPGateway
from src.engine.mcp.shutdown import read_shutdown_timeout
from src.engine.mcp.tools import register_mcp_tools
from src.engine.paths import providers_dir
from src.engine.preferences import PreferencesStore
from src.engine.providers.bootstrap import ProviderPaths, build_service
from src.engine.providers.factory.unconfigured import UnconfiguredModel
from src.engine.providers.login.chatgpt_flow import ChatGPTLoginFlow
from src.engine.providers.login.credential_source import chatgpt_provider_factory
from src.engine.providers.store.secret_store import FileSecretStore
from src.engine.providers.store.state_store import FileStateStore
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.tools import register_roadmap_tools
from src.engine.sessions.session_store import SessionStore
from src.engine.settings import SigilSettings, load_settings
from src.engine.shell.tools import register_shell_tools
from src.engine.skills.loaded import LoadedSkills
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.tools import register_skill_tools
from src.engine.themes import ThemeRegistry
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.web.tools import register_web_tools
from src.engine.workspace.manager import WorkspaceManager
from src.ui.app import SigilApp


def _active_provider(model_service: Any) -> str | None:
    """`system` of the model the next request goes to, or ``None`` if unknown.

    It is the same string a `ThinkingPart` records in `provider_name`, which is what
    decides whether its signature can be sent back. ``None`` disables the check
    rather than guessing: a wrong guess drops reasoning that was fine.
    """
    try:
        model = model_service.current_model()
    except Exception:  # noqa: BLE001 - the router can be unconfigured
        return None
    return getattr(model, 'system', None)


def _build_strategy(settings: SigilSettings):
    """View strategy based on configuration.

    - `windowed` (default): trims reasoning, old tool results and old turns.
    - `compacting`: also collapses old turns into a deterministic summary.
    - `passthrough`: sends the history as is.
    """
    if settings.context_strategy == 'passthrough':
        return PassthroughStrategy()
    if settings.context_strategy == 'compacting':
        return CompactingStrategy(
            keep_recent_turns=settings.keep_recent_turns,
            max_tool_result_chars=settings.max_tool_result_chars,
            reasoning=settings.reasoning,
            reasoning_steps=settings.reasoning_steps,
            reasoning_max_percent=settings.reasoning_max_percent,
            image_rounds=settings.image_rounds,
            image_max_percent=settings.image_max_percent,
        )
    return WindowedStrategy(
        keep_recent_turns=settings.keep_recent_turns,
        max_tool_result_chars=settings.max_tool_result_chars,
        reasoning=settings.reasoning,
        reasoning_steps=settings.reasoning_steps,
        reasoning_max_percent=settings.reasoning_max_percent,
        image_rounds=settings.image_rounds,
        image_max_percent=settings.image_max_percent,
    )


def _mcp_loader(config, logger, shutdown_timeout):
    """Builds the MCP layer, off the startup path.

    Returned as a callable so the heavy import happens on the worker thread that
    calls it, not here while the window is still waiting to be composed. The values
    it needs are captured now, while the environment is still being read here.
    """
    servers = config.servers

    def build():
        from src.engine.mcp.deferred import build_manager

        return build_manager(servers, logger, shutdown_timeout)

    return build


def run() -> None:
    # The only place the process environment is read. Everything below receives
    # values, so importing any module cannot freeze a setting or pull `.env` in.
    load_dotenv()
    settings = load_settings()

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

    meter = ContextMeter(output_reserve=settings.output_reserve)
    # Active session: the roadmap and the history hang off it.
    store = SessionStore()
    session = store.load_last()
    if session is None:
        session = store.create_session()

    # The agent profiles installed for this directory: the built-in set plus
    # whatever the project and the user added. The session remembers which one
    # it was using, so a resumed conversation comes back with its own agent.
    agent_registry = AgentRegistry(active=session.agent)

    # Providers: accounts, credentials and the model the agent talks to. The
    # router starts on a model that refuses to run until something is configured,
    # so the app opens and `/provider` is reachable from a cold start.
    provider_paths = ProviderPaths(providers_dir())
    secrets = FileSecretStore(provider_paths.credentials)
    state = FileStateStore(provider_paths.state)
    model_service = build_service(
        provider_paths,
        session_id=session.id,
        initial_model=UnconfiguredModel(),
        oauth_provider_factory=chatgpt_provider_factory(secrets),
    )
    selected = model_service.resolve(session.selection)
    if selected is not None:
        model_service.apply(selected)
        session.selection = model_service.state.last_selection()

    # The meter needs the window before the UI exists, or the first turn of a cold
    # start shows `ctx: ?` and cannot budget. The selection carries the last known
    # value, so this needs no network.
    meter.set_window(model_service.stored_window())

    # The context strategy is built once but the provider can be swapped mid-session,
    # so it reads the active one per request: signed reasoning is only replayed to
    # the provider that produced it.
    context_capability = ContextViewCapability(
        strategy=_build_strategy(settings),
        meter=meter,
        provider=lambda: _active_provider(model_service),
    )

    # Bridge for interactive questions and roadmap state: the engine
    # updates, the UI consumes them. The roadmap is per session: it is born empty with
    # each session and is recovered when loading it.
    question_hub = QuestionHub()
    roadmap_store = RoadmapStore(session_id=session.id)

    # Workspace: the directory Sigil works in, which can be a linked git worktree.
    # Switching re-points the components that cached the old root; the agent is
    # built once and closes over these same instances.
    workspace_manager = WorkspaceManager(
        workspace_root,
        session_store=store,
        roadmap_store=roadmap_store,
        rebindables=[scope, workspace, skill_registry],
    )

    # MCP servers declared in .sigil/config.json (and in ~/.sigil/config.json).
    # Config errors do not abort startup: they are shown in the chat. Parsing the
    # file is cheap and stays here; building the manager is not, and happens on a
    # worker once the window is up (see `MCPGateway`).
    config = load_config()
    mcp = MCPGateway(configured=len(config.servers), config_errors=tuple(config.errors))
    # How long a stdio server has to exit on EOF when the app quits. Read here,
    # with the rest of the settings, and handed to the loader as a value.
    mcp_shutdown_timeout = read_shutdown_timeout()

    # Retries on transient model failures. The instance is shared
    # with the UI so it announces every retry in the chat.
    model_retry = RetryTransientModelErrors(max_attempts=settings.model_max_attempts)

    # Every tool declares itself here as it is registered: the name, the permission it
    # serves and the model feature it needs. Two things read it — the capability that
    # hides tools the model cannot use, and the prompt, which must not describe a tool
    # the model will not see.
    tool_catalog = ToolCatalog()

    def supports(feature: str) -> bool:
        """Whether the active model has a feature, read per request."""
        if feature == IMAGE_INPUT:
            return model_service.snapshot().images
        return True

    def available_features() -> frozenset[str]:
        """What the model will see on the next request: tools and features."""
        profile = agent_registry.active
        features: set[str] = set()
        if supports(IMAGE_INPUT):
            features.add(IMAGE_INPUT)
        for declaration in tool_catalog.declarations():
            if not all(supports(feature) for feature in declaration.requires):
                continue
            groups = () if declaration.permission is None else declaration.permission.groups
            if profile is not None and not profile.permissions.advertises(declaration.name, groups):
                continue
            features.add(declaration.name)
        return frozenset(features)

    feature_gate = ModelFeatureGate(supports=supports)

    agent = create_agent(
        skill_registry,
        model=model_service.current_model(),
        # The placeholder, not the real toolsets: the agent is built before the
        # MCP layer exists, and `MCPGateway.install` fills it in.
        toolsets=[mcp.pending],
        model_retry=model_retry,
        workspace=workspace,
        scope=scope,
        loaded_skills=loaded_skills,
        agent_registry=agent_registry,
        extra_capabilities=[context_capability, feature_gate],
        available_features=available_features,
    )

    register_shell_tools(agent, logger=tool_logger, catalog=tool_catalog)
    register_skill_tools(agent, skill_registry, logger=tool_logger, loaded=loaded_skills, catalog=tool_catalog)
    register_mcp_tools(agent, mcp, logger=tool_logger, catalog=tool_catalog)
    register_web_tools(agent, logger=tool_logger, catalog=tool_catalog)
    register_file_tools(agent, logger=tool_logger, catalog=tool_catalog)
    register_image_tools(agent, logger=tool_logger, catalog=tool_catalog)
    register_interaction_tools(agent, question_hub, logger=tool_logger, catalog=tool_catalog)
    register_roadmap_tools(agent, roadmap_store, logger=tool_logger, catalog=tool_catalog)

    SigilApp(
        agent=agent,
        store=store,
        session=session,
        settings=settings,
        model_service=model_service,
        login_flow=ChatGPTLoginFlow(state=state),
        tool_logger=tool_logger,
        mcp_manager=mcp,
        startup_notices=(*config.errors,),
        mcp_loader=_mcp_loader(config, tool_logger, mcp_shutdown_timeout),
        question_hub=question_hub,
        roadmap_store=roadmap_store,
        model_retry=model_retry,
        meter=meter,
        context_capability=context_capability,
        scope=scope,
        workspace=workspace,
        loaded_skills=loaded_skills,
        workspace_manager=workspace_manager,
        # UI preferences live outside the session: they are the user's, not the
        # conversation's, and they have to be read before the first paint.
        preferences_store=PreferencesStore(),
        themes=ThemeRegistry.load(),
        agent_registry=agent_registry,
    ).run()
