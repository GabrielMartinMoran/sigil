"""The composition root: everything a client of Sigil needs, built once.

The engine is UI-agnostic and the application is not: the turn loop, the session,
the tree, the model router and the tools have to be wired together in one place,
and that place is here. The terminal client calls this and hands the result to
`SigilApp`; the web server calls the same thing and drives it from an event loop
with no widgets at all.

Two rules make that possible:

- **Nothing reads the environment twice.** `run()` (and the server entry point)
  loads `.env` and `load_settings()` once and passes the values down, so importing
  this module cannot freeze a setting.
- **The client owns its sinks.** The tool logger, the question hub and the
  workspace manager are passed in when they exist, so a client that paints tool
  rows or answers questions can attach itself without the core knowing how.

The object is a record of what was built, not a behaviour: the turn loop lives in
the client (`ui/turn_mixin.py` for the terminal, `server/runtime.py` for the web),
because it is the same decisions written twice for two transports and merging them
is the work the architecture page still has pending.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from src.engine.agents.factory import AgentFactory, AgentSpec
from src.engine.agents.model_features import IMAGE_INPUT, ModelFeatureGate
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.agents.rollback import Rollback
from src.engine.agents.tree.rendering import SUMMON_TOOL, render_peers, summonable
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.agents.tree.tools import register_tree_tools
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
from src.engine.providers.bootstrap import ProviderPaths, build_service
from src.engine.providers.domain.thinking import settings_value as thinking_settings
from src.engine.providers.factory.unconfigured import UnconfiguredModel
from src.engine.providers.login.chatgpt_flow import ChatGPTLoginFlow
from src.engine.providers.login.credential_source import chatgpt_provider_factory
from src.engine.providers.service import ModelService
from src.engine.providers.store.secret_store import FileSecretStore
from src.engine.providers.store.state_store import SqliteStateStore
from src.engine.roadmap.stores import RoadmapStores
from src.engine.roadmap.tools import register_roadmap_tools
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.settings import SigilSettings
from src.engine.shell.tools import register_shell_tools
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.tools import register_skill_tools
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.web.tools import register_web_tools
from src.engine.workspace.manager import WorkspaceManager


def build_strategy(settings: SigilSettings):
    """View strategy based on configuration.

    - `windowed` (default): trims reasoning, old tool results and old turns.
    - `compacting`: also collapses old turns into a deterministic summary.
    - `passthrough`: sends the history as is.
    """
    if settings.context_strategy == 'passthrough':
        return PassthroughStrategy()
    if settings.context_strategy == 'compacting':
        return CompactingStrategy(
            keep_recent_prompts=settings.keep_recent_prompts,
            max_tool_result_chars=settings.max_tool_result_chars,
            reasoning=settings.reasoning,
            reasoning_steps=settings.reasoning_steps,
            reasoning_max_percent=settings.reasoning_max_percent,
            image_turns=settings.image_turns,
            image_max_percent=settings.image_max_percent,
        )
    return WindowedStrategy(
        keep_recent_prompts=settings.keep_recent_prompts,
        max_tool_result_chars=settings.max_tool_result_chars,
        reasoning=settings.reasoning,
        reasoning_steps=settings.reasoning_steps,
        reasoning_max_percent=settings.reasoning_max_percent,
        image_turns=settings.image_turns,
        image_max_percent=settings.image_max_percent,
    )


def active_provider(model_service: Any) -> str | None:
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


def model_settings(model_service: Any) -> dict[str, Any] | None:
    """The reasoning level in effect, in the shape a run takes.

    Subagents run on the same level as the parent: it is the same account and the
    same model, and a subagent quietly reasoning less than the agent that summoned
    it would be a difference nobody asked for.
    """
    value = thinking_settings(model_service.snapshot().thinking)
    return None if value is None else {'thinking': value}


class Composition:
    """The engine of one workspace, wired and ready for a client to drive.

    Built by a client that already decided three things: where the workspace is
    (`root`), what it is configured with (`settings`), and which session it opens
    (`session`, or the last one used there).
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        settings: SigilSettings,
        session: Session | None = None,
        model: Any = None,
        tool_logger: ToolLoggerHub | None = None,
        question_hub: QuestionHub | None = None,
        provider_paths: ProviderPaths | None = None,
        login_flow: Any = None,
        on_plan_changed: Callable[[], None] | None = None,
    ) -> None:
        self.root = Path(os.path.realpath(str(root if root is not None else Path.cwd())))
        self.settings = settings
        self._model_override = model

        # A single skill registry shared by the agent instructions
        # (metadata) and by the tools (load, resources and scripts).
        self.skill_registry = SkillRegistry()

        # Tool logging hub: the engine emits, the client adds its sink.
        self.tool_logger = tool_logger if tool_logger is not None else ToolLoggerHub()

        # Context: the scope follows the directories the tools touch, and the
        # `workspace` block resolves AGENTS.md/CLAUDE.md over that tree.
        self.scope = ScopeTracker(self.root)
        self.workspace = WorkspaceInstructions(self.root)
        self.tool_logger.add_sink(ScopeSink(self.scope))

        self.meter = ContextMeter(output_reserve=settings.output_reserve)
        # Active session: the roadmap and the history hang off it.
        self.store = SessionStore(cwd=self.root)
        self.session = session if session is not None else self._open_session()

        # The agent profiles installed for this directory: the built-in set plus
        # whatever the project and the user added. The session remembers which one
        # it was using, so a resumed conversation comes back with its own agent.
        self.agent_registry = AgentRegistry(active=self.session.agent)

        # The tree of the session: the agent the user talks to at the root, and
        # every subagent summoned under it.
        self.agent_trees = AgentTreeStore()
        self.agent_trees.bind(self.session, root_name=self.agent_registry.active_name)

        # Providers: accounts, credentials and the model the agent talks to. The
        # router starts on a model that refuses to run until something is
        # configured, so a client opens and `/provider` is reachable cold.
        self.provider_paths = provider_paths if provider_paths is not None else ProviderPaths(providers_dir())
        secrets = FileSecretStore(self.provider_paths.credentials)
        state = SqliteStateStore(self.provider_paths.home)
        self.model_service: ModelService = build_service(
            self.provider_paths,
            session_id=self.session.id,
            initial_model=UnconfiguredModel(),
            oauth_provider_factory=chatgpt_provider_factory(secrets),
        )
        selected = self.model_service.resolve(self.session.selection)
        if selected is not None:
            self.model_service.apply(selected)
            self.session.selection = self.model_service.state.last_selection()

        # The meter needs the window before any client exists, or the first turn of
        # a cold start shows `ctx: ?` and cannot budget. The selection carries the
        # last known value, so this needs no network.
        self.meter.set_window(self.model_service.stored_window())

        # The context strategy is built once but the provider can be swapped
        # mid-session, so it reads the active one per request.
        self.context_capability = ContextViewCapability(
            strategy=build_strategy(settings),
            meter=self.meter,
            provider=lambda: active_provider(self.model_service),
        )

        # Bridge for interactive questions and roadmap state: the engine updates,
        # the client consumes them. The roadmap is per execution, not per session.
        self.question_hub = question_hub if question_hub is not None else QuestionHub()
        self.roadmap_store = RoadmapStores(
            session_id=self.session.id,
            flush=lambda: self.store.save(self.session) if self.session.messages else None,
        )

        # Rollback: the conversation, the plan and the workspace, back to the
        # instant before a turn started.
        self.rollback = Rollback(
            self.store,
            workspace=self.root,
            on_plan_changed=on_plan_changed or self.roadmap_store.reload_all,
        )

        # Workspace: the directory Sigil works in, which can be a linked git
        # worktree. Switching re-points the components that cached the old root;
        # the agent is built once and closes over these same instances.
        self.workspace_manager = WorkspaceManager(
            self.root,
            session_store=self.store,
            rebindables=[self.scope, self.workspace, self.skill_registry],
        )

        # MCP servers declared in .sigil/config.json (and in ~/.sigil/config.json).
        # Config errors do not abort startup: they are shown in the chat. Parsing
        # the file is cheap; building the manager is not and happens on a worker.
        config = load_config()
        self.mcp = MCPGateway(configured=len(config.servers), config_errors=tuple(config.errors))
        self.mcp_config = config
        self.mcp_shutdown_timeout = read_shutdown_timeout()
        self.login_flow = login_flow if login_flow is not None else ChatGPTLoginFlow(state=state)

        # Retries on transient model failures. The instance is shared with the
        # client so it can announce every retry.
        self.model_retry = RetryTransientModelErrors(max_attempts=settings.model_max_attempts)

        # Every tool declares itself here as it is registered: the name, the
        # permission it serves and the model feature it needs.
        self.tool_catalog = ToolCatalog()

        # Agents of the tree are built from the same parts as the root.
        self.summoner = SubagentSummoner(
            trees=self.agent_trees,
            registry=self.agent_registry,
            # Resolved per summon: the factory needs the summoner to install the
            # tool, and the summoner needs the factory to build a node.
            builder=lambda: self.factory.for_profile,
            max_steps=settings.max_steps,
            model=lambda: self.model_service.snapshot().label,
            model_settings=lambda: model_settings(self.model_service),
        )
        self.summoner.listen(
            on_change=self._on_tree_change,
            on_idle=self._tree_listeners_idle,
        )

        self.factory = self._build_factory()
        self.agent = self.factory.root()

    # --- the built pieces ---------------------------------------------------

    def _open_session(self) -> Session:
        session = self.store.load_last()
        return session if session is not None else self.store.create_session()

    def mcp_loader(self) -> Callable[[], Any] | None:
        """Builds the MCP layer off the startup path.

        Returned as a callable so the heavy import happens on the worker thread
        that calls it. The values it needs are captured now.
        """
        servers = self.mcp_config.servers
        logger = self.tool_logger
        timeout = self.mcp_shutdown_timeout

        def build():
            from src.engine.mcp.deferred import build_manager

            return build_manager(servers, logger, timeout)

        return build

    def current_model(self) -> Any:
        """The model the next agent is built on: an override, or the router's."""
        if self._model_override is not None:
            return self._model_override
        return self.model_service.current_model()

    def _build_factory(self) -> AgentFactory:
        return AgentFactory(
            AgentSpec(
                model=self.current_model(),
                skill_registry=self.skill_registry,
                # The placeholder, not the real toolsets: the agent is built before
                # the MCP layer exists, and `MCPGateway.install` fills it in.
                toolsets=[self.mcp.pending],
                model_retry=self.model_retry,
                workspace=self.workspace,
                scope=self.scope,
                agent_registry=self.agent_registry,
                extra_capabilities=[self.context_capability, self.feature_gate],
                tool_registrars=[self.install_tools],
                node_blocks=self.node_blocks,
                available_features=self.available_features,
            )
        )

    def rebuild_agent(self) -> Agent:
        """Builds a fresh root agent for the model in effect.

        Called after the model, the account or the profile changed: the agent
        closes over the model it was built with, and a session that swapped models
        must not keep sending to the old one.
        """
        self.factory = self._build_factory()
        self.agent = self.factory.root()
        return self.agent

    # --- derived state the prompt is rendered from --------------------------

    @property
    def feature_gate(self) -> ModelFeatureGate:
        if not hasattr(self, '_feature_gate'):
            self._feature_gate = ModelFeatureGate(supports=self.supports)
        return self._feature_gate

    def supports(self, feature: str) -> bool:
        """Whether the active model has a feature, read per request."""
        if feature == IMAGE_INPUT:
            return self.model_service.snapshot().images
        return True

    def available_features(self, agent_name: str | None = None) -> frozenset[str]:
        """What the model will see on the next request: tools and features.

        ``agent_name`` is the profile the request is for: ``None`` means the agent
        in effect (the root of the session).
        """
        registry = self.agent_registry
        profile = registry.active if agent_name is None else registry.get(agent_name)
        features: set[str] = set()
        if self.supports(IMAGE_INPUT):
            features.add(IMAGE_INPUT)
        for declaration in self.tool_catalog.declarations():
            if not all(self.supports(feature) for feature in declaration.requires):
                continue
            groups = () if declaration.permission is None else declaration.permission.groups
            if profile is not None and not profile.permissions.advertises(declaration.name, groups):
                continue
            # `summon` is only offered when there is someone to summon.
            if declaration.name == SUMMON_TOOL and profile is not None and not summonable(registry, profile):
                continue
            features.add(declaration.name)
        return frozenset(features)

    def node_blocks(self, node_id: str | None) -> str:
        """The instructions of one execution: the agents around it.

        Rendered per request from the node id of the run, because the tree moves
        under it: a sibling that was running when the subagent was summoned may
        have finished since, and a table of ids painted once would be stale facts.
        """
        tree = self.agent_trees.active
        node = tree.node(node_id)
        profile = self.agent_registry.get(node.name) if node is not None else self.agent_registry.active
        return render_peers(tree, node_id, profile=profile, is_live=self.summoner.is_live)

    def install_tools(self, target: Any) -> None:
        """Every tool every agent of the session gets. The root included."""
        logger = self.tool_logger
        catalog = self.tool_catalog
        register_shell_tools(target, logger=logger, catalog=catalog)
        register_skill_tools(target, self.skill_registry, logger=logger, catalog=catalog)
        register_mcp_tools(target, self.mcp, logger=logger, catalog=catalog)
        register_web_tools(target, logger=logger, catalog=catalog)
        register_file_tools(target, logger=logger, catalog=catalog)
        register_image_tools(target, logger=logger, catalog=catalog)
        register_interaction_tools(target, self.question_hub, logger=logger, catalog=catalog)
        register_roadmap_tools(target, self.roadmap_store, logger=logger, catalog=catalog)
        register_tree_tools(target, self.summoner, logger=logger)

    # --- tree listeners -----------------------------------------------------
    #
    # The summoner announces; the clients paint. A headless client may have
    # nothing to paint, so the default is silence.

    tree_changed: Callable[[Any], None] | None = None
    """Called with the node that changed, when a client wants to announce it."""

    _tree_listeners_idle: Callable[[], None] | None = None

    def _on_tree_change(self, node: Any) -> None:
        if self.tree_changed is not None:
            self.tree_changed(node)

    def on_idle(self, callback: Callable[[], None] | None) -> None:
        """Registers what happens when every subagent went quiet."""
        self._tree_listeners_idle = callback
        self.summoner.listen(
            on_change=self._on_tree_change,
            on_idle=callback,
        )

    def bind_session(self, session: Session) -> Session:
        """Points the composition at another session of the same workspace."""
        self.session = session
        self.agent_trees.bind(session, root_name=session.agent or self.agent_registry.active_name)
        self.roadmap_store.bind_session(session.id)
        return session

    def close(self) -> None:
        """Lets the summoner go: no listener outlives the client."""
        self.summoner.listen()
