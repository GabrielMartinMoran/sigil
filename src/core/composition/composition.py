"""The Composition value of the core layer."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from sigil_tools import ToolContext
from src.core.agents.agent_factory import AgentFactory
from src.core.agents.agent_model_config import AgentModelConfig
from src.core.agents.agent_spec import AgentSpec
from src.core.agents.config_scope import ConfigScope
from src.core.agents.model_defaults import read_defaults, save_default
from src.core.agents.model_defaults_state import ModelDefaultsState
from src.core.agents.model_feature_gate import IMAGE_INPUT, ModelFeatureGate
from src.core.agents.profile.agent_registry import AgentRegistry
from src.core.agents.profile.models.agent_not_found import AgentNotFound
from src.core.agents.retry_transient_model_errors import RetryTransientModelErrors
from src.core.agents.rollback import Rollback
from src.core.agents.tree.agent_run_model import AgentRunModel
from src.core.agents.tree.agent_tree_store import AgentTreeStore
from src.core.agents.tree.rendering import SUMMON_TOOL, render_notifications, render_peers, summonable
from src.core.agents.tree.subagent_summoner import SubagentSummoner
from src.core.agents.tree.summoner import current_agent_model
from src.core.agents.tree.tools import register_tree_tools
from src.core.attachments.tools import register_image_tools
from src.core.codemode.tools import register_code_tools
from src.core.composition.active_provider import active_provider
from src.core.composition.build_strategy import build_strategy
from src.core.composition.model_settings import model_settings
from src.core.config.paths import providers_dir
from src.core.config.sigil_settings import SigilSettings
from src.core.context.context_view_capability import ContextViewCapability
from src.core.context.instructions.workspace_instructions import WorkspaceInstructions
from src.core.context.meter.context_meter import ContextMeter
from src.core.context.scope_sink import ScopeSink
from src.core.context.scope_tracker import ScopeTracker
from src.core.extensions.extension_toolset import ExtensionToolset
from src.core.files.paths import workspace_root
from src.core.files.tools import register_file_tools
from src.core.interaction.models.question_item import QuestionItem
from src.core.interaction.models.question_option import QuestionOption
from src.core.interaction.question_hub import QuestionHub
from src.core.interaction.tools import register_interaction_tools
from src.core.mcp.config.loader import load_config
from src.core.mcp.manager.mcp_manager import MCPManager
from src.core.mcp.mcp_gateway import MCPGateway
from src.core.mcp.shutdown import read_shutdown_timeout
from src.core.mcp.tools import register_mcp_tools
from src.core.providers.bootstrap import build_service
from src.core.providers.domain.provider_error import ProviderError
from src.core.providers.domain.selection import Selection
from src.core.providers.domain.thinking import settings_value as thinking_settings
from src.core.providers.factory.unconfigured_model import UnconfiguredModel
from src.core.providers.login.chatgpt_login_flow import ChatGPTLoginFlow
from src.core.providers.login.credential_source import chatgpt_provider_factory
from src.core.providers.model_service import ModelService
from src.core.providers.model_snapshot import ModelSnapshot
from src.core.providers.provider_paths import ProviderPaths
from src.core.providers.store.file_secret_store import FileSecretStore
from src.core.providers.store.session_state_store import SessionStateStore
from src.core.providers.store.sqlite_state_store import SqliteStateStore
from src.core.roadmap.roadmap_stores import RoadmapStores
from src.core.roadmap.tools import register_roadmap_tools
from src.core.sessions.session import Session
from src.core.sessions.session_store import SessionStore
from src.core.shell.running_commands import RunningCommands
from src.core.shell.tools import register_shell_tools
from src.core.skills.skill_registry import SkillRegistry
from src.core.skills.tools import register_skill_tools
from src.core.tooling.tool_catalog import ToolCatalog
from src.core.tooling.tool_logger_hub import ToolLoggerHub
from src.core.web.tools import register_web_tools
from src.core.workspace.workspace_manager import WorkspaceManager


def _known_window(model: object, fallback: int | None) -> int | None:
    """The window the model itself reports, or ``fallback`` when it reports none.

    The built model carries the profile it was given, and pydantic-ai's bundled
    profiles know models no catalogue of ours has described — a cold start, an
    offline machine, a model released after the last fetch. The meter and the panel
    already read the window from there (`load_model_window`), so a run is captured
    with the same source; reading only the catalogue left a subagent with ``None``
    and every one of its requests was refused as unbudgetable.
    """
    window = getattr(model, 'context_window', None)
    return window if window else fallback


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
        self.shell_commands = RunningCommands()
        self.mcp_config = load_config(cwd=self.root)
        self._agent_config_root = self.root

        # A single skill registry shared by the agent instructions
        # (metadata) and by the tools (load, resources and scripts). It discovers
        # against the composition's workspace: the launch directory may be
        # somewhere else, and the project skills belong to the root the session
        # works in. An explicit `SIGIL_SKILLS_DIRS` is left alone by `rebind`.
        self.skill_registry = SkillRegistry()
        self.skill_registry.rebind(self.root)

        # Tool logging hub: the engine emits, the client adds its sink.
        self.tool_logger = tool_logger if tool_logger is not None else ToolLoggerHub()

        # Context: the scope follows the directories the tools touch, and the
        # `workspace` block resolves AGENTS.md/CLAUDE.md over that tree.
        self.scope = ScopeTracker(self.root)
        self.workspace = WorkspaceInstructions(self.root)
        self.tool_logger.add_sink(ScopeSink(self.scope))

        self.meter = ContextMeter(
            output_reserve=settings.output_reserve, safety_margin_percent=settings.context_safety_margin_percent
        )
        # Active session: the roadmap and the history hang off it.
        self.store = SessionStore(cwd=self.root)
        self.session = session if session is not None else self._open_session()

        # The agent profiles installed for this directory: the built-in set plus
        # whatever the project and the user added. The session remembers which one
        # it was using, so a resumed conversation comes back with its own agent.
        self.agent_registry = AgentRegistry(active=self.session.agent, cwd=self.root)
        self.agent_registry.on_rebind = lambda: self.factory.clear_profiles()

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
        self.model_service.state = SessionStateStore(self.model_service.state)
        # The model the conversation runs on. The session's own wins, and a session
        # that has none gets the default chain. It is the same path every `/load`
        # takes, so a conversation opened cold and one opened mid-run agree — and
        # changing a model later reaches this session and no other.
        if model is None:
            active = self.agent_registry.active_name
            if active in self.mcp_config.invalid_agents:
                self.mcp_config = replace(
                    self.mcp_config,
                    errors=(
                        *self.mcp_config.errors,
                        f'Invalid model configuration for agent {active!r}; check the config errors.',
                    ),
                )
            self.apply_session_model(self.session)
        # The meter needs the window before any client exists, or the first turn of
        # a cold start shows `ctx: ?` and cannot budget. The selection carries the
        # last known value, so this needs no network.
        self.meter.set_window(self.model_service.stored_window())
        self.model_service.on_window = self.meter.set_window
        # The fixed blocks were measured by the request that produced them and are
        # written on the row; without them the budget would claim room that the
        # tool schemas and the instructions are already using.
        self.meter.restore(self.session)

        # Every tool declares itself here as it is registered: the name, the
        # permission it serves, the model feature it needs and what it keeps of its
        # arguments and results. The context view reads the last one, so the catalogue
        # has to exist before the capability that projects the history.
        self.tool_catalog = ToolCatalog()

        # The context strategy is built once but the provider can be swapped
        # mid-session, so it reads the active one per request.
        self.context_capability = ContextViewCapability(
            strategy=build_strategy(settings, lookup=self.tool_catalog.compactor),
            meter=self.meter,
            provider=self.current_provider,
        )

        # Bridge for interactive questions and roadmap state: the engine updates,
        # the client consumes them. The roadmap is per execution, not per session.
        self.question_hub = question_hub if question_hub is not None else QuestionHub()
        self.roadmap_store = RoadmapStores(
            session_id=self.session.id,
            flush=lambda: self.store.save_progress(self.session) if self.session.messages else None,
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
            rebindables=[self.scope, self.workspace, self.skill_registry, self.agent_registry],
        )

        # MCP servers declared in .sigil/config.json (and in ~/.sigil/config.json).
        # Config errors do not abort startup: they are shown in the chat. Parsing
        # the file is cheap; building the manager is not and happens on a worker.
        config = self.mcp_config
        self.mcp = MCPGateway(configured=len(config.servers), config_errors=tuple(config.errors))
        self.mcp.set_overrides(self.session.mcp_overrides)
        self.mcp_config = config
        self.mcp_shutdown_timeout = read_shutdown_timeout()
        self.login_flow = login_flow if login_flow is not None else ChatGPTLoginFlow(state=state)

        # Retries on transient model failures. The instance is shared with the
        # client so it can announce every retry.
        self.model_retry = RetryTransientModelErrors(max_attempts=settings.model_max_attempts)

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
            agent_model=self.subagent_model,
            base_model=self.root_run_model,
        )
        self.summoner.listen(
            on_change=self._on_tree_change,
            on_idle=self._tree_listeners_idle,
        )

        self.extensions = ExtensionToolset(
            workspace=lambda: self.workspace_manager.root,
            catalog=self.tool_catalog,
            authorize=self._authorize_extensions,
            logger=self.tool_logger,
            context_factory=self._extension_context,
            notice=lambda text: self.extension_notice(text) if self.extension_notice else None,
        )
        self.factory = self._build_factory()
        self.agent = self.factory.root()

    extension_notice: Callable[[str], None] | None = None

    async def _authorize_extensions(self, workspace: Path) -> bool:
        if not self.question_hub.available:
            return False
        answers = await self.question_hub.ask(
            [
                QuestionItem(
                    question=(
                        f'Load Python tool extensions from {workspace / ".sigil" / "tools"}? '
                        'This executes trusted code with your user permissions, without a sandbox.'
                    ),
                    options=[
                        QuestionOption(label='Do not load', recommended=True),
                        QuestionOption(label='Load extensions'),
                    ],
                    allow_custom=False,
                )
            ]
        )
        return bool(answers and not answers[0].custom and answers[0].answer == 'Load extensions')

    def _extension_context(self, ctx: Any) -> ToolContext:
        tree = self.agent_trees.active
        node_id = getattr(ctx.deps, 'node_id', None)
        node = tree.node(node_id) if node_id else tree.root
        return ToolContext(
            workspace=self.workspace_manager.root,
            agent_id=node.id if node is not None else '',
            agent_name=node.name if node is not None else self.agent_registry.active_name,
            session_id=self.session.id,
        )

    def _require_declared_compactors(self) -> None:
        # A missing declaration is not a fallback to the generic policy: it is a tool
        # nobody decided the cost of, and the place to notice is the build, not a bill.
        missing = self.tool_catalog.uncovered()
        if missing:
            listed = ', '.join(missing)
            raise RuntimeError(f'tools without a declared compactor: {listed}')

    # --- the built pieces ---------------------------------------------------

    def _open_session(self) -> Session:
        return self.store.draft_session()

    def mcp_loader(self) -> Callable[[], Any] | None:
        """Builds the MCP layer off the startup path.

        Returned as a callable so the heavy import happens on the worker thread
        that calls it. The values it needs are captured now.
        """
        servers = self.mcp_config.servers
        logger = self.tool_logger
        timeout = self.mcp_shutdown_timeout

        def build() -> MCPManager:
            from src.core.mcp.deferred import build_manager

            return build_manager(servers, logger, timeout)

        return build

    def select_agent_model(self, name: str) -> ModelSnapshot | None:
        """Apply a profile's default without preventing later manual changes."""
        if name in self.mcp_config.invalid_agents:
            raise ProviderError(f'Invalid model configuration for agent {name!r}; check the config errors.')
        config = self.mcp_config.agents.get(name)
        if config is None:
            return None
        return self.model_service.select(config.account, config.model, config.effort, preserve_thinking=True)

    async def subagent_model(self, name: str) -> AgentRunModel | None:
        """Resolve the profile's catalogue before building its independent model."""
        if name in self.mcp_config.invalid_agents:
            raise ProviderError(f'Invalid model configuration for agent {name!r}; check the config errors.')
        config = self.mcp_config.agents.get(name)
        if config is None:
            return None
        # The cache is per process and account; opening a picker in another client
        # does not populate it here. Fetch before building so the wire profile also
        # receives the provider's window, image support and API dialect.
        await self.model_service.catalog_for(config.account)
        model = self.model_service.build_agent_model(config.account, config.model)
        value = thinking_settings(config.effort)
        settings = None if value is None else {'thinking': value}
        capability = self.model_service.agent_capability(config.account, config.model)
        return AgentRunModel(
            model,
            settings,
            f'{config.account}:{config.model}',
            _known_window(model, capability.context_window),
            capability.supports_images,
            getattr(model, 'system', None),
        )

    def root_run_model(self) -> AgentRunModel:
        """Capture the root selection when a child is started."""
        snapshot = self.model_service.snapshot()
        model = self._model_override if self._model_override is not None else self.model_service.router.current
        return AgentRunModel(
            model,
            model_settings(self.model_service),
            snapshot.label,
            _known_window(model, snapshot.window),
            snapshot.images,
            getattr(model, 'system', None),
        )

    def current_provider(self) -> str | None:
        selected = current_agent_model()
        return selected.provider if selected is not None else active_provider(self.model_service)

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
                toolsets=[self.mcp.pending, self.extensions],
                model_retry=self.model_retry,
                workspace=self.workspace,
                scope=self.scope,
                agent_registry=self.agent_registry,
                extra_capabilities=[self.context_capability, self.feature_gate],
                tool_registrars=[self.install_tools],
                node_blocks=self.node_blocks,
                available_features=self.available_features,
                allow_external_files=lambda: self.session.allow_external_files,
                root=self.execution_root,
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

    # --- what a client records after a selection changed --------------------

    def apply_model_change(self, snapshot: Any) -> Any:
        """Records a new model selection on the session, the meter and the store.

        This is the one place that decides what "the model changed" means, so the
        two clients cannot disagree about it: the session carries the selection
        (a restart resumes it), the meter budgets against the window the new model
        reports, and a conversation that already has messages is written so the
        row matches the screen. A session with no messages is left alone on
        purpose — saving it would create an empty conversation nobody asked for.

        The caller that *serves turns* must call `rebuild_agent` afterwards: the
        agent closes over the model it was built with (see `_build_factory`), so a
        server whose next turn reads `Composition.agent` would otherwise keep
        sending to the previous model. Whether the terminal also needs it is a
        property of the terminal (it holds its own agent), not of this method, so
        the rebuild is the caller's decision and is not done here.

        The terminal's `ProviderMixin._after_model_change` still has its own copy
        of these lines. That copy stays for this cut because the terminal is not
        migrated yet: it is the only client actually in use, and moving a path
        nothing here exercises would be an unverified change to it. The copy
        disappears in `tui-attach-v1`, when the terminal stops mutating state
        locally and calls this instead.
        """
        self.session.selection = snapshot.selection
        if self.session.messages:
            self.store.save_progress(self.session)
        window = getattr(self.agent.model, 'context_window', None)
        self.meter.set_window(window if window else snapshot.window)
        return snapshot

    def apply_agent(self, name: str) -> tuple[Any, Any]:
        """Activates a profile and stamps it on the session and the root of the tree.

        Returns the profile and the model snapshot the profile's own default
        produced (``None`` when the profile declares no model in the config), which
        is the pair a client reports. A profile whose model cannot be resolved puts
        the previous agent back before the error leaves: a refused switch must not
        leave the session on an agent that never ran.

        Like `apply_model_change`, the terminal's `AgentMixin.apply_agent` keeps its
        own copy for this cut — same reason: it is not migrated yet, and
        `tui-attach-v1` is where that copy goes.
        """
        previous = self.agent_registry.active_name
        try:
            profile = self.agent_registry.activate(name)
            snapshot = self.select_agent_model(profile.name)
        except AgentNotFound, ProviderError:
            if previous and self.agent_registry.active_name != previous:
                self.agent_registry.activate(previous)
            raise
        self.session.agent = profile.name
        self.agent_trees.rename_root(profile.name)
        if snapshot is not None:
            self.apply_model_change(snapshot)
        self._persist_idle()
        return self.agent_registry.active or profile, snapshot

    # --- tree actions -------------------------------------------------------

    def stop_subagent(self, node_id: str) -> str:
        """Stops a subagent and everything it summoned.

        The decision is the summoner's — the same object the terminal's tree
        calls — so an unknown id, the session's own agent and a node that cannot
        be stopped are refused with the engine's own reasons, not a second rule.

        Raises:
            SummonError: when the node cannot be stopped.
        """
        return self.summoner.kill(None, node_id)

    def background_subagent(self, node_id: str) -> str:
        """Moves a running subagent to the background.

        A plain delegation: the refusals — an unknown id, the session's own agent,
        a node that already finished — are the summoner's, so the terminal's tree
        action and this one cannot disagree about them.

        Raises:
            SummonError: when the node cannot be sent to the background.
        """
        return self.summoner.detach(node_id)

    # --- session settings ---------------------------------------------------

    def apply_external_files(self, allow: bool) -> Session:
        """Allows or denies file access outside the workspace for this session.

        The flag is read per tool call, so the change is effective immediately and
        needs no rebuild; it is written down so the next launch keeps it.
        """
        self.session.allow_external_files = bool(allow)
        self._persist_idle()
        return self.session

    async def apply_mcp_override(self, name: str, enabled: bool) -> Session:
        """Enables or disables one MCP server for this session.

        The choice is remembered on the session's overrides and handed to the
        gateway, which is what changes the tool set of the next turn; disabling
        also disconnects the server instead of leaving it running. This is the
        decision the terminal's `/mcp` screen makes, moved here so both clients
        cannot disagree about it.

        Refused while a subagent is still working, which is the terminal's second
        criterion for this command: a live node runs on an agent whose tools were
        computed when it started, so disabling a server would close the transport
        under a call already in flight, and enabling one would swap the tool set
        mid-run. The terminal keeps its own copy of this check until it stops
        binding state locally (`tui-attach-v1`).

        Raises:
            RuntimeError: while a subagent of this session is running.
            LookupError: when the config declares no server with that name.
        """
        if self.summoner.running():
            raise RuntimeError('Subagents are still working: stop them before you change MCP servers.')
        if name not in self.mcp_config.servers:
            raise LookupError(f'No MCP server named {name!r} is configured.')
        self.session.mcp_overrides[name] = enabled
        self.mcp.set_overrides(self.session.mcp_overrides)
        self._persist_idle()
        if not enabled:
            await self.mcp.stop_disabled(name)
        return self.session

    def _persist_idle(self) -> None:
        """Writes the session, adopting a concurrent winner instead of raising.

        A toggle over HTTP must not become a server error because another client
        wrote the same conversation first, and an empty draft has nothing to
        write: `save` refuses it and `save_current` keeps the object.
        """
        current = self.store.save_current(self.session)
        if current is not self.session:
            # Adopting the winner's row must not re-resolve the model: the turn or
            # the command that got here already chose it, and a background rebind
            # that silently moved the router would be a second, invisible decision.
            self.bind_session(current, model=False)

    # --- derived state the prompt is rendered from --------------------------

    @property
    def feature_gate(self) -> ModelFeatureGate:
        if not hasattr(self, '_feature_gate'):
            self._feature_gate = ModelFeatureGate(supports=self.supports)
        return self._feature_gate

    def supports(self, feature: str) -> bool:
        """Whether the active model has a feature, read per request."""
        if feature == IMAGE_INPUT:
            selected = current_agent_model()
            return selected.images if selected is not None else self.model_service.snapshot().images
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
        """The request context of one execution: the agents around it.

        Rendered per request from the node id of the run, because the tree moves
        under it: a sibling that was running when the subagent was summoned may
        have finished since, and a table of ids painted once would be stale facts.
        """
        tree = self.agent_trees.active
        node = tree.node(node_id)
        profile = self.agent_registry.get(node.name) if node is not None else self.agent_registry.active
        return '\n'.join(
            block
            for block in (
                render_peers(tree, node_id, profile=profile, is_live=self.summoner.is_live),
                render_notifications(tree, node_id),
            )
            if block
        )

    def execution_root(self) -> Path:
        """The root the tools resolve against for the execution in flight.

        A turn binds the workspace it selected (see
        `src.core.files.paths.execution_workspace`), so tools, permissions and
        shell commands follow that turn. Outside a run — a command typed between
        turns — the composition's own workspace is the fallback.
        """
        return workspace_root(self.workspace_manager.root)

    def install_tools(self, target: Any) -> None:
        """Every tool every agent of the session gets. The root included."""
        logger = self.tool_logger
        catalog = self.tool_catalog
        register_shell_tools(
            target, logger=logger, catalog=catalog, root=self.execution_root, owner=self.shell_commands
        )
        register_code_tools(target, logger=logger, catalog=catalog)
        register_skill_tools(target, self.skill_registry, logger=logger, catalog=catalog)
        register_mcp_tools(target, self.mcp, logger=logger, catalog=catalog)
        register_web_tools(target, logger=logger, catalog=catalog)
        register_file_tools(
            target,
            logger=logger,
            catalog=catalog,
            allow_external=lambda: self.session.allow_external_files,
            root=self.execution_root,
        )
        register_image_tools(target, logger=logger, catalog=catalog, root=self.execution_root)
        register_interaction_tools(target, self.question_hub, logger=logger, catalog=catalog)
        register_roadmap_tools(target, self.roadmap_store, logger=logger, catalog=catalog)
        register_tree_tools(target, self.summoner, logger=logger, catalog=catalog)
        self._require_declared_compactors()

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

    def bind_session(self, session: Session, *, model: bool = True) -> Session:
        """Points the composition at another session of the same workspace.

        The model comes with it. A conversation's model is part of the conversation —
        changing it here changes it for this session and for no other — so binding is
        also where it is restored, in one place, for every client.
        """
        self.agent_registry.restore(session.agent)
        replaced_profile = session.agent != self.agent_registry.active_name
        session.agent = self.agent_registry.active_name
        self.session = session
        self.mcp.set_overrides(session.mcp_overrides)
        # The measured half of the context travels on the session, because the
        # request parameters it came from do not survive the process.
        self.meter.restore(session)
        workspace = Path(os.path.realpath(session.cwd))
        if workspace != self._agent_config_root:
            self._agent_config_root = workspace
            self.reload_agent_models()
        self.agent_trees.bind(session, root_name=session.agent or self.agent_registry.active_name)
        self.roadmap_store.bind_session(session.id)
        if model:
            self.apply_session_model(session)
        if replaced_profile:
            self.store.save(session)
        return session

    def apply_session_model(self, session: Session) -> ModelSnapshot | None:
        """Points the router at the model the conversation runs on, and records it.

        The session's own selection wins. When it has none — a session created before
        models were stored with a conversation, or one that was never used — the
        default chain decides, and its answer is written back so the session keeps it.

        Nothing here raises: a session whose account was removed must still open, and
        the conversation keeps the model currently in effect. The client says so.
        """
        if self.model_service.is_busy():
            return None
        selection = session.selection
        if selection is None or self.model_service.accounts.find_id(selection.account_id) is None:
            selection = self.default_selection(session.agent or self.agent_registry.active_name)
        if selection is None:
            return None
        try:
            snapshot = self.model_service.apply(selection)
        except ProviderError:
            return None
        session.selection = snapshot.selection
        return snapshot

    def model_defaults_state(self) -> ModelDefaultsState:
        """What the two config files say about default models, per file.

        Read on every call, never cached: the screen writes to one of these files and
        then shows the result, and a cached copy would show its own memory instead of
        what is on disk — including an edit made by hand while the app was open.
        """
        return read_defaults(cwd=self._agent_config_root)

    def save_model_default(
        self, target: str | None, config: AgentModelConfig | None, scope: ConfigScope
    ) -> tuple[str, ...]:
        """Writes one default and re-reads the config that reads it.

        ``target`` is an agent name, or ``None`` for the block a conversation starts
        on; ``config`` is ``None`` to clear the entry. The re-read is what makes the
        write take effect without a restart: `default_selection`, `select_agent_model`
        and `subagent_model` all read `mcp_config`, and they read it next time.
        """
        errors = save_default(target, config, scope, cwd=self._agent_config_root)
        if not errors:
            self.reload_agent_models()
        return errors

    def reload_agent_models(self) -> tuple[str, ...]:
        """Re-reads `agents` and `default_model` for the workspace in effect.

        Only those two: the MCP servers are already running, and re-reading them here
        would leave `mcp_config` describing a set of servers that nothing started.
        """
        config = load_config(cwd=self._agent_config_root)
        self.mcp_config = replace(
            self.mcp_config,
            agents=config.agents,
            invalid_agents=config.invalid_agents,
            default_model=config.default_model,
        )
        return config.errors

    def default_selection(self, agent_name: str) -> Selection | None:
        """What a conversation starts on when nothing has been chosen for it.

        The order is the whole feature: the agent's own model, then `default_model`
        from the Sigil config, then the last model used. A profile that names a model
        always wins over the global default, and the global default always wins over
        a memory of the last session.
        """
        chosen = self._config_selection(self.mcp_config.agents.get(agent_name))
        if chosen is not None:
            return chosen
        chosen = self._config_selection(self.mcp_config.default_model)
        if chosen is not None:
            return chosen
        return self.model_service.resolve(None)

    def _config_selection(self, config: AgentModelConfig | None) -> Selection | None:
        """A configured `{account, model, effort}` as a selection, if its account exists."""
        if config is None:
            return None
        account = next((item for item in self.model_service.accounts.load() if item.alias == config.account), None)
        if account is None:
            return None
        capability = self.model_service.agent_capability(account.alias, config.model)
        return Selection(
            account_id=account.id,
            model=config.model,
            thinking=config.effort,
            window=capability.context_window,
            efforts=capability.efforts,
            images=capability.supports_images,
        )

    def close(self) -> None:
        """Lets the summoner go: no listener outlives the client."""
        self.summoner.listen()
