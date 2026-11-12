"""Sigil terminal UI built with Textual.

The app is the UI composition point: it builds the widget tree and delegates
each responsibility to a mixin with its own state.

- `ModelViewMixin` : the active model and effort in effect, read by the chrome.
- `AgentMixin`     : `/agent`, the `ctrl+a` picker and the agent in the status bar.
- `TreeMixin`      : the session's tree of agents: `ctrl+t`, the subagent view and
                     the messages a finished subagent sends back.
- `SidebarMixin`   : side panel: active model, context budget, skills, MCP servers.
- `MenuMixin`      : command menu that appears when typing `/`.
- `CommandsMixin`  : slash commands and session switching.
- `ProviderMixin`  : `/provider`, `/model`, `/effort` and their screens.
- `PreferencesMixin` : this client's preferences: theme, reasoning, tool output.
- `ActivityMixin`  : the status bar: spinner, workspace and diff counters.
- `DebugMixin`     : agent debug view (`alt+d`), which replaces the chat.
- `TurnMixin`      : turn execution and on-disk checkpoints.
- `TitleMixin`     : the session title and the terminal window title.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import Any, ClassVar

from pydantic_ai import Agent
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches

from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.retry import RetryNotice, RetryTransientModelErrors
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.context.capability import ContextViewCapability
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.scope import ScopeTracker
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_option import QuestionOption
from src.engine.mcp.manager.manager import MCPManager
from src.engine.preferences import Preferences, PreferencesStore
from src.engine.providers.service import ModelService
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.settings import SigilSettings, load_settings
from src.engine.shell import running as running_commands
from src.engine.storage.database import database_path
from src.engine.themes import ThemeRegistry
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.tooling.presentation import ToolSummary
from src.engine.workspace.manager import WorkspaceManager
from src.ui.activity_mixin import ActivityMixin
from src.ui.agent_mixin import AgentMixin
from src.ui.commands_mixin import CommandsMixin
from src.ui.debug_mixin import DebugMixin
from src.ui.desktop_notifications import Attention, NotificationContext, send_notification
from src.ui.menu_mixin import MenuMixin
from src.ui.messages import ChatNotice, ModelRetryNotice, RoadmapChanged
from src.ui.model_view_mixin import ModelViewMixin
from src.ui.preferences_mixin import PreferencesMixin
from src.ui.provider_mixin import ProviderMixin
from src.ui.rollback_mixin import RollbackMixin
from src.ui.service_client import (
    NoHistoryYet,
    ServiceClient,
    ServiceClientError,
    ServiceEvent,
    ServiceUnavailable,
    StreamInterrupted,
)
from src.ui.sidebar_mixin import SidebarMixin
from src.ui.title_mixin import TitleMixin
from src.ui.tool_logging import ChatToolLogSink, ToolLogMessage
from src.ui.tree_mixin import TreeMixin
from src.ui.turn_mixin import TurnMixin, failure_notice
from src.ui.widgets.agent_view import AgentView
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar
from src.ui.workspace_mixin import WorkspaceMixin

SERVICE_EVENTS = 'service-events'
"""Worker group of the loop that follows the service's event stream."""

SERVICE_HISTORY = 'service-history'
"""Worker group of the read that paints the conversation the service already has."""

SERVICE_RECONNECT_DELAY = 0.5
"""Seconds before re-following a stream that ended. Doubles, up to a ceiling."""

SERVICE_RECONNECT_CEILING = 5.0
"""The longest pause between two attempts to follow the service again."""

TONE_STYLES = {'danger': 'bold red', 'warn': 'yellow', 'muted': 'dim'}
"""How the service's notice tones are painted. An unknown tone is a warning."""


def _question_item(raw: dict[str, Any]) -> QuestionItem:
    """One question of the service's payload, as the panel's own model."""
    return QuestionItem(
        question=str(raw.get('question') or ''),
        allow_custom=bool(raw.get('allow_custom', True)),
        options=[
            QuestionOption(
                label=str(option.get('label') or ''),
                description=option.get('description'),
                recommended=bool(option.get('recommended')),
            )
            for option in raw.get('options') or []
        ],
    )


class _ServiceOwnedSession:
    """What ``_store`` is on an attached terminal: the row belongs to the service.

    The composer refreshes the session before sending, so a change made by another
    process is not overwritten. A terminal attached to the service has no local copy to
    refresh — the service is the only writer — and that is exactly what this answers.
    Nothing else is offered on purpose: a local write is the second executor this cut
    removes, so it must fail loudly rather than pretend to work.
    """

    def __init__(self, session: Session) -> None:
        self.cwd = session.cwd
        self.base_dir = database_path().parent

    def refresh(self, session: Session) -> None:
        """Nothing local can be newer than the service's own row."""
        return


class SigilApp(
    ModelViewMixin,
    AgentMixin,
    SidebarMixin,
    MenuMixin,
    CommandsMixin,
    ProviderMixin,
    PreferencesMixin,
    WorkspaceMixin,
    ActivityMixin,
    DebugMixin,
    TreeMixin,
    RollbackMixin,
    TurnMixin,
    TitleMixin,
    App,
):
    """Terminal chat: scrollable history, fixed input, menu and side panel."""

    CSS_PATH = 'theme.tcss'
    TITLE = 'Sigil'
    ENABLE_COMMAND_PALETTE = False

    # Focus lives in the input, which swallows the keys. The pickers therefore
    # read `ctrl+m` / `ctrl+l` in `PromptInput._on_key` instead of here: a global
    # binding would claim Enter on terminals that cannot tell it from Ctrl+M.
    #
    # The two that are bound here open a *view* over the chat — the debug view and
    # the tree of agents — and have to be reachable with the focus anywhere,
    # including the composer while it is disabled. The tree sits on `ctrl+t`, which
    # used to open the effort picker: reading the squad is the more frequent act, and
    # `ctrl+l` was free.
    #
    # There is no global `escape` binding on purpose: the focused widget sees the
    # key first, and the abort has to coexist with the `escape` that cancels a
    # question panel. The prompt handles it (see `PromptInput`).
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('alt+d', 'toggle_debug', 'Debug view', priority=True, show=False),
        Binding('ctrl+t', 'toggle_tree', 'Agents', priority=True, show=False),
    ]

    def __init__(
        self,
        agent: Agent | None = None,
        store: SessionStore | None = None,
        session: Session | None = None,
        settings: SigilSettings | None = None,
        model_service: ModelService | None = None,
        login_flow: Any = None,
        tool_logger: ToolLoggerHub | None = None,
        mcp_manager: MCPManager | None = None,
        startup_notices: Sequence[str] = (),
        mcp_loader: Callable[[], MCPManager] | None = None,
        question_hub: QuestionHub | None = None,
        roadmap_store: RoadmapStores | None = None,
        rollback: object | None = None,
        model_retry: RetryTransientModelErrors | None = None,
        meter: ContextMeter | None = None,
        context_capability: ContextViewCapability | None = None,
        scope: ScopeTracker | None = None,
        workspace: WorkspaceInstructions | None = None,
        workspace_manager: WorkspaceManager | None = None,
        preferences: Preferences | None = None,
        preferences_store: PreferencesStore | None = None,
        themes: ThemeRegistry | None = None,
        agent_registry: AgentRegistry | None = None,
        agent_tree: AgentTreeStore | None = None,
        summoner: SubagentSummoner | None = None,
        on_bind_session: Callable[[Session], object] | None = None,
        on_agent_selected: Callable[[str], Any] | None = None,
        extensions: Any = None,
        cancel_shell: Callable[[], int] | None = None,
        client: ServiceClient | None = None,
    ) -> None:
        """Builds the terminal over a service, or — in tests only — over the engine.

        Exactly one of ``client`` and the in-process ``agent``/``store`` pair is
        allowed, and both are enforced here rather than documented and hoped for.

        ``client`` is the production shape: the service owns the session, the queue
        and the turn, and this app paints what the service says and sends it what the
        user types. `src/sigil.py` builds nothing else, and it composes no engine, so
        the in-process path is not merely unused in production — it has nothing to
        pass.

        The in-process collaborators are the seam the existing app tests mount. They
        are marked, mutually exclusive and unreachable from the entry point on
        purpose: while the remaining mixins still own their own collaborators, this is
        the only way to keep ~40 widget tests honest without rewriting them. It is a
        real seam and not a hidden fallback — a second way to execute a turn is
        exactly what this cut exists to remove.
        """
        if client is not None and (agent is not None or store is not None):
            raise ValueError(
                'A terminal attached to the service executes nothing: pass a client, or in-process '
                'collaborators for a widget test, never both.'
            )
        if client is None and (agent is None or store is None):
            raise ValueError('A terminal needs a service client, or in-process collaborators for a widget test.')
        if session is None:
            raise ValueError('The terminal needs the conversation it is painting.')
        super().__init__()
        self._client = client
        # What the service last said the screen should show, and where following it
        # got to. The state is the projection a delta can never replace.
        self._service_state: dict[str, Any] | None = None
        self._service_cursor = 0
        self._service_following = False
        self._service_question: str | None = None
        self._service_answered: set[str] = set()
        self._service_steers: list[Any] = []
        self._service_pending: list[Any] = []
        # Set when another client accepted a message while this screen was mid-turn:
        # rebuilding the transcript then would drop the deltas streaming into the
        # block being written, so the rebuild waits for the end of the turn.
        self._service_rebuild_due = False
        self._service_reconnect_delay = SERVICE_RECONNECT_DELAY
        self._service_request = None
        self._cancel_shell = cancel_shell if cancel_shell is not None else running_commands.kill_all
        self._extensions = extensions
        self._settings = settings or load_settings({})
        self._agent = agent
        self._store = store
        self._session = session
        self._on_bind_session = on_bind_session
        self._on_agent_selected = on_agent_selected
        self._model_service = model_service
        self._login_flow = login_flow
        self._tool_logger = tool_logger
        self._mcp_manager = mcp_manager
        if mcp_manager is not None and hasattr(mcp_manager, 'set_overrides'):
            mcp_manager.set_overrides(session.mcp_overrides)
        self._mcp_loader = mcp_loader
        self._startup_notices = tuple(startup_notices)
        self._question_hub = question_hub
        self._roadmap_store = roadmap_store
        self._rollback = rollback
        self._model_retry = model_retry
        self._meter = meter or ContextMeter(output_reserve=self._settings.output_reserve)
        self._context_capability = context_capability
        self._scope = scope
        self._workspace = workspace
        self._workspace_manager = workspace_manager
        # Preferences are read here, not in `on_mount`: the reasoning preference
        # decides how the first painted transcript looks.
        self._preferences_store = preferences_store or PreferencesStore()
        self._preferences = preferences or self._preferences_store.load()
        self._themes = themes or ThemeRegistry.load()
        self._agent_registry = agent_registry
        # The tree of the session and the object that summons subagents into it.
        # Both are optional: an app built without them still runs the conversation,
        # and the tree shortcut says there is no tree instead of failing.
        self._agent_tree = agent_tree
        self._summoner = summoner
        self._viewed_node: str | None = None
        # The timer that turns a finished subagent into a turn nobody asked for.
        self._wake_timer: Any = None
        self._wake_pending: bool = False
        self._tool_log_sink: ChatToolLogSink | None = None
        self._history: list[Any] = list(session.messages)
        self.menu_visible = False
        # Debug view: it replaces the chat, it does not coexist with it.
        self.debug_visible = False
        self._debug_timer: Any = None
        self._debug_pending: list[Any] | None = None
        # Run in progress: `checkpoint` uses it to persist turn progress.
        self._current_run: Any = None
        self._last_checkpoint_at = 0.0
        # Status bar: the spinner and the diff summary.
        self._activity_running = False
        self._activity_label = ''
        self._turn_started_at = 0.0
        self._last_diff = None
        self._diff_timer: Any = None
        # A turn is accepted the moment the message is submitted, before the
        # worker gets its turn on the event loop. `_turn_active` is what the
        # prompt asks to know whether a message starts a turn or steers one.
        self._turn_active = False
        # Messages typed while the agent works, in order, so they can be
        # delivered to the run and, if one misses, given back to the prompt.
        self._steering: list[Any] = []
        # The session the current turn belongs to (`checkpoint` writes there).
        self._turn_session: Any = None
        # Abort confirmation: `Esc` asks once, the second press inside the
        # window stops the turn.
        self._abort_deadline: float | None = None
        self._abort_timer: Any = None
        # The window title: what was last written to the terminal (the terminal
        # cannot be read back), how many times naming each session was tried and
        # the worker of the request in flight, so an unnameable session does not
        # cost a request per message and a slow one is not asked for twice.
        self._painted_title = ''
        self._naming_attempts: dict[str, int] = {}
        self._naming_workers: dict[str, Any] = {}
        if client is not None:
            # What the unmigrated mixins expect of a local store, answered by the
            # service instead of a second copy of its row, and the commands that would
            # write locally refused where the user can read the refusal.
            self._store = _ServiceOwnedSession(session)
            self.handle_command = self._refuse_command  # type: ignore[method-assign]
        self._wire_busy_flag()

    def _wire_busy_flag(self) -> None:
        """Lets the service refuse a model swap while a turn is running."""
        if self._model_service is not None:
            self._model_service.is_busy = lambda: self._turn_active

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                SidebarPanel(self._roadmap_store, id='sidebar'),
                Vertical(
                    ChatLog(
                        id='chat',
                        reasoning_visible=self._preferences.reasoning_visible,
                        tools_expanded=self._preferences.tools_expanded,
                    ),
                    AgentDebugView(id='debug'),
                    AgentView(id='agent'),
                    id='conversation',
                ),
                id='main',
            ),
            # The questions take the place of the composer, not a slot inside the
            # conversation column: they span the whole width and sit exactly where
            # the user expects to act (see `ask_question`). While one is open the
            # composer and the side panel step aside.
            QuestionPanel(id='questions'),
            CommandMenu(id='menu'),
            PromptInput(placeholder='Message…  Enter sends, Shift+Enter adds a line', id='prompt'),
            StatusBar(id='status'),
            id='root',
        )

    # --- lifecycle ---------------------------------------------------------

    async def on_mount(self) -> None:
        # Themes first: everything painted after this point uses the user's palette.
        self.install_themes()
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        if self._tool_logger is not None:
            self._tool_log_sink = ChatToolLogSink(self)
            self._tool_logger.add_sink(self._tool_log_sink)
        if self._question_hub is not None:
            self._question_hub.set_asker(self.ask_question)
        if self._model_retry is not None:
            self._model_retry.on_retry = self.notify_model_retry
        if self._roadmap_store is not None:
            self._roadmap_store.add_listener(self.handle_roadmap_store_change)
            self._point_roadmap_at_view()
        self.query_one(CommandMenu).display = False
        self.query_one(AgentView).display = False
        # The listeners of the tree belong to the app: it is what paints a node and
        # what can hand a finished subagent's message to the running turn.
        if self._summoner is not None:
            self._summoner.listen(
                on_change=self.announce_tree_change,
                root_run=self.root_run,
                on_delivered=self.announce_subagent_message,
                on_idle=self.subagents_idle,
            )
        # The session carries the agent it runs with from the first paint, so a
        # conversation saved before any switch still remembers it. An attached
        # terminal does not own that row: the service does, and it stamps the agent
        # there. Writing it from here would overwrite what the service said.
        if self._client is None:
            self.remember_agent(self.active_agent())
        self.refresh_sidebar()
        self.boot_mcp()
        self.refresh_workspace_badge()
        self.refresh_diff()
        self._start_diff_polling()
        for error in self.agent_notices():
            await self.query_one(ChatLog).add_notice(f'✖ {error}', style='yellow')
        for notice in self._startup_notices:
            await self.query_one(ChatLog).add_notice(notice, style='yellow')
        for error in self._themes.errors:
            await self.query_one(ChatLog).add_notice(f'Theme skipped: {error}', style='yellow')
        if self._history:
            chat = self.query_one(ChatLog)
            await chat.render_history(self._history, subagents=self.tree_nodes())
            chat.finalize()
        self.query_one(PromptInput).focus()
        self.load_model_window()
        self.resolve_model_capabilities()
        # The window says where and what from the first paint, before any message
        # has given the session a name: the repository half is already known.
        self.paint_window_title()
        # Everything above painted from what this process knows. An attached
        # terminal knows nothing yet, so it asks once, paints what the service
        # already has, starts the stream after exactly what that answer contained,
        # and then follows it.
        if self._client is not None:
            await self.refresh_service_state()
            self._service_cursor = self._state_cursor()
            self.follow_service_events()
            # On a worker and not here: mounting widgets while `on_mount` is still
            # awaiting needs the message pump, and the pump is the thing waiting for
            # `on_mount` to return.
            self.run_worker(self.load_service_history(), group=SERVICE_HISTORY)

    async def on_unmount(self) -> None:
        self._stop_diff_polling()
        # The stream is the service's; leaving is the client's. The flag stops the
        # loop at its next iteration, and cancelling the worker closes the
        # connection now instead of after the next event.
        self._service_following = False
        self.workers.cancel_group(self, SERVICE_EVENTS)
        if self._wake_timer is not None:
            self._wake_timer.stop()
            self._wake_timer = None
        if self._summoner is not None:
            self._summoner.listen()
        self.theme_changed_signal.unsubscribe(self)
        if self._model_retry is not None:
            self._model_retry.on_retry = None
        if self._tool_logger is not None and self._tool_log_sink is not None:
            self._tool_logger.remove_sink(self._tool_log_sink)
            self._tool_log_sink = None
        if self._question_hub is not None:
            self._question_hub.set_asker(None)
        if self._roadmap_store is not None:
            self._roadmap_store.remove_listener(self.handle_roadmap_store_change)
        # Last, and awaited: the MCP probe leaves a kept-alive session per server
        # open on purpose, and the event loop waits them out when it shuts down.
        # Closing them here is what keeps the quit from stalling on a server that
        # ignores the EOF (see `src.engine.mcp.shutdown`).
        if self._extensions is not None:
            self.workers.cancel_all()
            if self._summoner is not None:
                self._summoner.cancel_all()
            await self._extensions.close()
        await self.close_mcp_servers()

    @work(exclusive=True, group='mcp-boot')
    async def boot_mcp(self) -> None:
        """Builds the MCP layer off the startup path, then probes it.

        Building the manager imports ``pydantic_ai.mcp`` and the whole MCP SDK —
        half a second that used to happen before the first paint. It runs here
        instead, on a thread, while the window is already up.

        The thread only *builds*. Installing the toolsets and probing the servers
        happen back on this loop, because that is the loop that enters, uses and
        closes them.
        """
        gateway = self._mcp_manager
        loader = self._mcp_loader
        if gateway is None or not gateway:
            return
        if loader is None:
            # A manager handed over already built (the tests, and any caller that
            # prefers to build it itself): there is nothing to load, only to probe.
            self.refresh_mcp_status()
            return
        try:
            manager = await asyncio.to_thread(loader)
        except Exception as exc:  # noqa: BLE001 - a failed load is not a failed app
            gateway.fail(f'MCP unavailable: {type(exc).__name__}: {exc}')
            await self.query_one(ChatLog).add_notice(f'✖ MCP unavailable: {exc}', style='yellow')
            return
        gateway.install(manager)
        for error in manager.errors:
            await self.query_one(ChatLog).add_notice(f'✖ {error}', style='yellow')
        # The probe connects and lists the real tools, which is what the panel
        # and `/mcp` report. It is a worker of its own: scheduling it is enough.
        self.refresh_mcp_status()

    # --- theme changes -----------------------------------------------------

    def _on_theme_changed(self, _theme: Any) -> None:
        """Repaints the text the stylesheet cannot reach.

        Textual repaints the boxes on a theme change, but the Rich text built in
        Python (the panel sections, the tool gutters, the diff counters) is cached
        per widget and keeps the old palette. Rebuilding it is what makes the live
        preview in `/theme` actually live instead of only recoloring the borders.
        """
        with suppress(NoMatches):
            self.query_one(SidebarPanel).refresh_palette()
        with suppress(NoMatches):
            self.query_one(ChatLog).repaint()
        with suppress(NoMatches):
            self.query_one(StatusBar).refresh()
        # The node view is not composed in every app (the tests build them without
        # one), and the mixin already ignores the absence.
        self.run_worker(self.repaint_agent_view())

    # --- diff polling ------------------------------------------------------

    def _start_diff_polling(self) -> None:
        """Reads the diff periodically while the app runs.

        Polling, not pushing: a tool can change the tree through a shell command
        the file tools never see, so there is no event to subscribe to. The worker
        is skipped entirely outside a repository, where the bar shows nothing.
        """
        if self._workspace_manager is None:
            return
        if self._diff_timer is None:
            from src.ui.activity_mixin import DIFF_REFRESH_INTERVAL

            self._diff_timer = self.set_interval(DIFF_REFRESH_INTERVAL, self.refresh_diff)

    def _stop_diff_polling(self) -> None:
        if self._diff_timer is not None:
            self._diff_timer.stop()
            self._diff_timer = None

    # --- tool and model events ---------------------------------------------

    async def on_chat_notice(self, message: ChatNotice) -> None:
        """Appends a notice posted from a synchronous context."""
        await self.query_one(ChatLog).add_notice(message.text, style=message.style)
        if 'red' in message.style:
            self.notify(message.text, severity='error')

    async def on_prompt_input_attachment_rejected(self, message: PromptInput.AttachmentRejected) -> None:
        """An image could not be attached: one line, and the composer keeps what it had.

        The reason is the whole point of the line. "Pasted without an image" would
        leave the user pressing the key again on a clipboard that will not work
        until a package is installed, so the message names the fix.
        """
        await self.query_one(ChatLog).add_notice(f'✖ {message.reason}', style='yellow')

    async def on_tool_log_message(self, message: ToolLogMessage) -> None:
        event = message.event
        if event.node_id is not None:
            return
        chat = self.query_one(ChatLog)
        if event.phase == 'start':
            await chat.add_tool_call(event.tool_name, event.args)
            self.set_activity_label(f'Running {event.tool_name}')
        elif event.phase == 'error':
            await chat.add_tool_error(event.tool_name, event.error or 'failed')
        else:
            # The result itself arrives later, on the stream. This is the tool saying
            # how it went (`9 passed in 0.45s`) and how long it took, which the stream
            # does not carry.
            chat.set_tool_summary(event.tool_name, event.summary, event.duration_ms)
            # A call may have loaded a skill, and the sidebar repaints when the derived
            # set changed. No name of a tool is needed here: what is loaded is a fact of
            # the transcript, and `load_skill` is only how it got there.
            self.refresh_skills()
        # A tool call is the moment the tree can change: the bar catches up now
        # instead of on the next poll.
        if event.phase != 'start':
            self.refresh_diff()
        # The debug view follows the turn, just like the chat.
        self.refresh_debug()

    # --- the service ------------------------------------------------------

    async def _refuse_command(self, name: str, args: str = '') -> None:
        """A slash command in an attached terminal, answered instead of attempted.

        The command handlers reach for the store and the engine this terminal does not
        own, so one of them would fail somewhere the user cannot see — in a worker,
        after the composer has already cleared itself. Saying it plainly is both more
        honest and more useful: the conversation works, the commands move over with
        their own cut.

        Async, matching `handle_command`: the notice is appended by awaiting the chat
        log, so a refusal that was never awaited would paint nothing at all — which is
        the one outcome a user cannot tell apart from a command that worked.
        """
        await self.query_one(ChatLog).add_notice(
            f"{name} still runs in the terminal's own process, and this terminal is attached to the "
            'service. The conversation works; the commands move over in a later cut.',
            style='yellow',
        )

    @work(exclusive=True, group=SERVICE_EVENTS)
    async def follow_service_events(self) -> None:
        """Follows the service from the last event applied, reconnecting by cursor.

        The cursor is the service's sequence number, so a reconnection asks for what
        it missed instead of starting over. A break is reported rather than swallowed:
        a screen that stopped following looks exactly like a turn with nothing more to
        say, and that is the one failure a user cannot see.

        The loop yields the event loop on every read, so the terminal keeps painting
        while a turn streams for minutes.
        """
        client = self._client
        if client is None:
            return
        self._service_following = True
        delay = self._service_reconnect_delay
        try:
            while self._service_following:
                try:
                    async for event in client.events(after=self._service_cursor):
                        if event.id is not None:
                            self._service_cursor = event.id
                        delay = self._service_reconnect_delay
                        await self.apply_service_event(event)
                    # The stream ended because this client closed it: nothing to do.
                    return
                except StreamInterrupted as exc:
                    if exc.last_event_id is not None:
                        self._service_cursor = exc.last_event_id
                    await self.report_stream_gap()
                except ServiceClientError as exc:
                    await self.report_service_trouble(exc)
                    if isinstance(exc, ServiceUnavailable) and self._service_following:
                        # Nothing answered. The likeliest cause is a service that
                        # restarted on another port, so look for it before dialling
                        # the same dead address again.
                        await self.resume_after_restart()
                if not self._service_following:
                    return
                await asyncio.sleep(delay)
                delay = min(max(delay * 2, SERVICE_RECONNECT_DELAY), SERVICE_RECONNECT_CEILING)
        finally:
            self._service_following = False

    async def resume_after_restart(self) -> None:
        """Finds the service again, and restarts the cursor from its state.

        A restarted service is a different run: another port, another token, and — the
        part that is easy to miss — a sequence space that starts again at one. Reusing
        the cursor this screen had against the old run would ask the new service for
        events *after* a number it has not reached yet, and the terminal would follow a
        live conversation while silently skipping everything published before it caught
        up. So the cursor comes from a fresh state, exactly as it does on the first
        connection, and the transcript is repainted from the rows of the service that is
        actually running.

        Quiet on failure on purpose: `report_service_trouble` has already said the
        service is not answering, and repeating it once per retry would fill the
        transcript with the same sentence.
        """
        client = self._client
        if client is None:
            return
        try:
            if not await client.refresh_endpoint():
                return
        except ServiceClientError:
            return
        self._service_cursor = 0
        await self.refresh_service_state()
        self._service_cursor = self._state_cursor()
        await self.repaint_from_rows()

    async def report_stream_gap(self) -> None:
        """One sentence about a stream that broke, then the state, which cannot be wrong.

        A gap means an unknown number of deltas never arrived, so the transcript is
        rebuilt from the durable rows instead of kept with a hole in it: a conversation
        that is missing one tool result and does not say so is worse than one that was
        painted again from the truth.
        """
        await self.query_one(ChatLog).add_notice(
            'The event stream from the service broke, so events may be missing. '
            'Reconnecting from the last one and re-reading the state.',
            style='yellow',
        )
        await self.refresh_service_state()
        await self.load_service_history(rebuild=True)

    async def report_service_trouble(self, exc: ServiceClientError) -> None:
        """The service is not answering: say what to do, and keep trying quietly."""
        await self.query_one(ChatLog).add_notice(f'✖ {failure_notice(exc)}', style='bold red')
        if not isinstance(exc, ServiceUnavailable):
            # An unauthorized or unreadable reply will not improve by waiting.
            self._service_following = False

    async def apply_service_event(self, event: ServiceEvent) -> None:
        """Applies one event to the screen.

        An event type this build does not know is ignored on purpose: the service adds
        types by adding them, and a terminal that failed on a newer one would break a
        working conversation for no reason.
        """
        chat = self.query_one(ChatLog)
        data = event.data
        if event.type == 'text_delta':
            await chat.add_text(str(data.get('text') or ''))
            self.set_activity_label('Writing')
        elif event.type == 'thinking_delta':
            await chat.add_thinking(str(data.get('text') or ''))
            self.set_activity_label('Thinking')
        elif event.type == 'tool_call':
            await chat.add_tool_call(str(data.get('tool_name') or ''), data.get('args'))
            self.set_activity_label(f'Running {data.get("tool_name") or "tool"}')
        elif event.type == 'tool_result':
            await chat.add_tool_result(
                str(data.get('tool_name') or ''), data.get('preview') or '', ok=bool(data.get('ok', True))
            )
        elif event.type == 'tool_finished':
            await self._paint_tool_end(data, chat)
        elif event.type == 'turn_started':
            self._turn_active = True
            await chat.begin_response()
            self.set_activity(running=True, label='Thinking')
        elif event.type == 'turn_finished':
            await self._finish_turn(data, chat)
        elif event.type == 'notice':
            await chat.add_notice(
                str(data.get('text') or ''), style=TONE_STYLES.get(str(data.get('tone') or ''), 'yellow')
            )
        elif event.type == 'question':
            identifier = str(data.get('id') or '')
            if identifier and self._service_question is None:
                # Set here and not inside the worker: the panel must be claimed before
                # the next glance at the state can offer the same question twice.
                self._service_question = identifier
                self.run_worker(self.answer_service_question(identifier, data.get('questions') or []))
        elif event.type == 'question_closed':
            self._close_service_question(str(data.get('id') or ''))
        elif event.type == 'steer_delivered':
            await self._service_steer_delivered()
        elif event.type == 'tree_changed':
            # The tree panel is a later cut; the state is where it will read from.
            await self.refresh_service_state()
        elif event.type == 'state_changed':
            await self.refresh_service_state()
            if str(data.get('reason') or '') == 'queue':
                # Another client accepted a message. The event carries no text: the
                # rows do, so this screen re-reads them instead of being told twice.
                await self.refresh_service_queue()
        elif event.type == 'lagged':
            await chat.add_notice(
                'The service says this terminal fell behind, so events may be missing. Re-reading the state.',
                style='yellow',
            )
            await self.refresh_service_state()

    async def _paint_tool_end(self, data: dict[str, Any], chat: ChatLog) -> None:
        """A finished call: the tool's own summary, or the reason it raised."""
        name = str(data.get('tool_name') or '')
        if data.get('phase') == 'error':
            await chat.add_tool_error(name, str(data.get('error') or 'failed'))
            return
        summary = data.get('summary')
        chat.set_tool_summary(
            name,
            None
            if summary is None
            else ToolSummary(text=str(summary.get('text') or ''), status=str(summary.get('status') or 'ok')),
            data.get('duration_ms'),
        )
        self.refresh_skills()
        self.refresh_diff()

    async def _finish_turn(self, data: dict[str, Any], chat: ChatLog) -> None:
        """The end of a turn: refetch, then repaint what the refetch cannot carry."""
        await self.refresh_service_state()
        self._turn_active = False
        self.set_activity(running=False)
        chat.finalize()
        if self._service_rebuild_due:
            # A message another client sent during this turn was deferred, not lost:
            # the stream has stopped, so the rows can be painted without dropping text.
            await self.refresh_service_queue()
        status = str(data.get('status') or 'ok')
        if status == 'cancelled':
            await chat.add_notice('Turn cancelled.', style='yellow')
        elif status == 'error':
            await chat.add_notice(f'Error: {data.get("error") or "the turn failed"}', style='bold red')
        self._restore_prompt_focus()

    async def answer_service_question(self, question_id: str, raw: list[dict[str, Any]]) -> None:
        """Shows the questions the service is holding and answers them over HTTP.

        The panel is the one the in-process path uses, down to its keys: what changed
        is where the answer goes. ``None`` — cancelled — resolves the question with
        nothing, which is how the service stops the turn where it stands.

        The question stays claimed until the answer has been sent. Releasing it before
        that would let a state read taken in between offer the same question again, and
        two panels for one question is a screen asking the user to answer twice.
        """
        try:
            answers = await self.ask_question([_question_item(item) for item in raw])
            payload = (
                None
                if answers is None
                else [
                    {'question': answer.question, 'answer': answer.answer, 'custom': answer.custom}
                    for answer in answers
                ]
            )
            try:
                resolved = await self._client.answer(question_id, payload)
            except ServiceClientError as exc:
                await self.query_one(ChatLog).add_notice(f'✖ {failure_notice(exc)}', style='bold red')
                return
            if not resolved:
                # Answered elsewhere, or withdrawn when the turn ended: the state says
                # which, and it is the only thing that knows.
                await self.refresh_service_state()
        finally:
            self._service_answered.add(question_id)
            self._service_question = None

    def _close_service_question(self, question_id: str) -> None:
        """The service withdrew its question: the panel stops asking."""
        if not question_id or self._service_question != question_id:
            return
        self._service_question = None
        panel = self._pending_question()
        if panel is not None:
            panel.action_cancel()

    async def _service_steer_delivered(self) -> None:
        """The run read one of the waiting messages: its line stops waiting.

        Two kinds of line can be waiting, and they are retired differently on purpose.
        One painted from the durable read is *removed*: the run has now read it, so it
        belongs to the transcript, and the rebuild that follows the confirmation paints
        it there — keeping both would show the same message twice. One typed in this
        window keeps its place and only loses the queued mark, which is what the live
        turn has always done.

        Confirmations arrive in queue order, so the oldest waiting line is the one that
        was read; the event carries the service's own enqueue id, not the request id this
        terminal generated, and inventing a mapping would be worse than the ordering.
        """
        if self._service_pending:
            widget = self._service_pending.pop(0)
            with suppress(NoMatches):
                await widget.remove()
            return
        if not self._service_steers:
            return
        widget = self._service_steers.pop(0)
        with suppress(NoMatches):
            self.query_one(ChatLog).mark_delivered(widget)

    async def refresh_service_queue(self) -> None:
        """Re-reads the durable transcript when another client accepted a message.

        The service publishes the acceptance and not the text — the rows are the text
        — so the screen that did not type the message learns it by reading them. An
        append is not an option: the message is already part of the history this
        screen was given, and adding it again is exactly the duplication the queue's
        identity rules exist to prevent. The transcript is rebuilt instead.

        Mid-turn the repair waits. ``chat.clear()`` would drop the deltas streaming
        into the block being written, so the flag is left set and `_finish_turn` does
        it once the stream has stopped.
        """
        await self.repaint_from_rows()

    async def repaint_from_rows(self) -> None:
        """Repaints the transcript from the durable rows, or waits for the turn to stop.

        Clearing the chat would drop the deltas streaming into the block being written,
        so mid-turn the repair is deferred: the flag stays set and `_finish_turn` does
        it once the stream has stopped.
        """
        if self._client is None:
            return
        if self._turn_active:
            self._service_rebuild_due = True
            return
        self._service_rebuild_due = False
        await self.load_service_history(rebuild=True)

    async def refresh_service_state(self) -> None:
        """Re-reads everything the screen paints from the service's rows.

        A delta is never the only copy of a fact: a lagged client, a reconnection and
        the end of a turn all come back here. That is what keeps the screen honest
        after events it may have missed, and it is why the state carries a sequence
        number instead of relying on the deltas adding up.
        """
        client = self._client
        if client is None:
            return
        try:
            state = await client.state()
        except ServiceClientError as exc:
            await self.query_one(ChatLog).add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        self._apply_service_state(state)

    async def load_service_history(self, *, rebuild: bool = False) -> None:
        """Paints the conversation the service already has, from its durable read.

        Attaching to a session with history used to leave the transcript empty until the
        next event arrived: the stream starts after the state on purpose, and the state
        carries counts rather than messages. `/api/session/durable` is the one read that
        answers both halves at once — what was said, and what is still waiting in the
        queue — so the screen starts complete instead of growing one message at a time.

        ``rebuild`` is the reconnection case: the stream broke, so the transcript may be
        missing deltas and repainting it from the rows is the honest repair.

        A session with no row yet answers 404 and becomes `NoHistoryYet`: the screen
        stays empty and keeps following, because a conversation whose first message has
        not been written has no history to show and nothing to apologise for.

        The queue entries the service reports as pending are painted *queued* and kept,
        never merged into the transcript: a message the run has not read is not part of
        the conversation yet, and painting it twice is what would happen if the stream
        later delivered it as history.
        """
        client = self._client
        if client is None:
            return
        try:
            durable = await client.durable()
        except NoHistoryYet:
            return
        except ServiceClientError as exc:
            await self.query_one(ChatLog).add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        chat = self.query_one(ChatLog)
        if rebuild:
            await chat.clear()
        self._service_pending = await chat.render_items(
            durable.get('transcript') or [], pending=durable.get('pending') or ()
        )

    def _state_cursor(self) -> int:
        """Where the stream starts the first time: the sequence number of the state.

        A terminal that opened `/events` at zero would be painted every event the
        service still remembers, most of them things the state it just read already
        describes — a transcript painted twice. The reconnection path is the other way
        round on purpose: there the cursor is the last event applied, because what was
        missed is exactly what matters.
        """
        state = self._service_state or {}
        try:
            return max(0, int(state.get('seq') or 0))
        except TypeError, ValueError:
            return self._service_cursor

    def _apply_service_state(self, state: dict[str, Any]) -> None:
        """Paints the projection: what is running, what it is called, what is being asked."""
        self._service_state = state
        busy = bool(state.get('busy'))
        self._turn_active = busy
        # The spinner follows the service, not this process: a terminal that attached
        # mid-turn has to look like one that started it. Only on a change, or every
        # refresh would reset the elapsed clock the bar is showing.
        if busy != self._activity_running:
            self.set_activity(running=busy, label='Thinking' if busy else '')
        window = (state.get('model') or {}).get('window')
        if isinstance(window, int) and window > 0:
            self._meter.set_window(window)
        session = state.get('session') or {}
        title = session.get('title')
        if self._session is not None and isinstance(title, str):
            self._session.title = title
        self.refresh_sidebar()
        self.refresh_agent()
        self.paint_window_title()
        question = state.get('question')
        if isinstance(question, dict):
            identifier = str(question.get('id') or '')
            if identifier and identifier not in self._service_answered and self._service_question is None:
                self._service_question = identifier
                self.run_worker(self.answer_service_question(identifier, question.get('questions') or []))

    # --- debug view -------------------------------------------------------

    def action_toggle_debug(self) -> None:
        """`alt+d`: replaces the chat with the agent debug view."""
        if self.subagent_open:
            # The two views replace the same space. Opening the debug view closes
            # the node one here and not on a worker: a frame with both is a frame
            # with two conversations and no way to tell which one is on screen.
            self.show_chat()
        self.toggle_debug()

    def action_toggle_tree(self) -> None:
        """`ctrl+t`: the agents of the session, and the way into any subagent."""
        self.run_worker(self.open_tree())

    def action_tab_noop(self) -> None:
        """Tab outside the command menu does nothing.

        It exists so `PromptInput` always has a target for the key: without it the
        focus would move to another widget and the prompt would stop receiving text.
        """

    async def key_escape(self) -> None:
        """`Esc` when the prompt is not the one that read it.

        The prompt handles it while it has the focus (`PromptInput`), a question
        panel handles it while a question is open, and this is the fallback: with
        the focus on the chat or anywhere else, the abort still reaches you.

        A question outranks the abort. While one is open the panel owns `Esc`, and
        this path is the case where the key did not reach the panel's binding —
        nothing is focused, or a widget in between ate it. Aborting the turn there
        kills the run and leaves the question on screen waiting for an answer that
        can never come; cancelling the question is what the user meant.

        It is a `key_escape` method and not a binding on purpose: a non-priority
        binding would show up in the help as a second way to do the same thing,
        and a priority one would run before the question panel and swallow the
        `Esc` that cancels a question.
        """
        panel = self._pending_question()
        if panel is not None:
            panel.action_cancel()
            return
        if self.request_abort():
            self.abort_turn()
            return
        # Not aborting and not running: `Esc` inside a subagent is the way back,
        # which is the one navigation key that has to work with the composer
        # disabled.
        if not self._turn_active and self.subagent_open:
            await self.open_parent()

    async def on_key(self, event: events.Key) -> None:
        """Any key that is not the confirming `Esc` cancels a pending abort.

        The prompt does this for the keys it sees, but focus can be somewhere
        else (the chat, the debug view). A confirmation that survives a keypress
        anywhere in the app is a confirmation by accident.
        """
        if event.key == 'escape':
            return
        self.cancel_abort()

    async def on_model_retry_notice(self, message: ModelRetryNotice) -> None:
        notice = message.notice
        await self.query_one(ChatLog).add_notice(
            f'↻ Transient model failure ({notice.reason}); '
            f'retry {notice.next_attempt}/{notice.max_attempts} in {notice.delay:.1f}s',
            style='yellow',
        )

    def notify_model_retry(self, notice: RetryNotice) -> None:
        """Retry capability callback: the handler paints the notice."""
        self.post_message(ModelRetryNotice(notice))

    # --- roadmap ---------------------------------------------------------

    def handle_roadmap_store_change(self) -> None:
        """Store listener. It must not be called ``_on_*``: Textual treats that
        prefix as a private message handler and would re-post in a loop."""
        self.post_message(RoadmapChanged())

    def on_roadmap_changed(self, message: RoadmapChanged) -> None:
        self.query_one(SidebarPanel).refresh_from_store()

    def notify_attention(self, event: Attention) -> None:
        """Desktop delivery never takes the keyboard or blocks the message pump."""
        if not self.is_headless and self._preferences.desktop_notifications:
            context = NotificationContext(
                self.repository_label(), self._session.title or f'Session {self._session.short_id}'
            )
            self._deliver_attention(event, context)

    @work(thread=True, group='desktop-notifications', exit_on_error=False)
    def _deliver_attention(self, event: Attention, context: NotificationContext) -> None:
        if not send_notification(event, context):
            self.log.debug('Desktop notification unavailable or rejected')

    # --- interactive questions --------------------------------------------

    async def ask_question(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        """Shows the questions where the composer is, and waits for the answers.

        It is not a modal screen: the questions usually refer to what is in the
        conversation, and that has to stay readable while answering. What it does
        instead is take the composer's place — the panel spans the whole width,
        right where the user expects to act — so the thing that owns the keyboard
        is the thing on screen, and there is no dead input sitting below it.

        The composer is hidden rather than emptied: a half-typed message survives
        and comes back with the focus when the question closes.

        The columns above give up **height**, not their place. The panel is a sibling
        of `#main`, so it takes its room from that whole row: the transcript and the
        side panel shrink together and both stay where they were. The side panel
        scrolls, so a shorter one is still usable, and its roadmap is often what the
        question is about.

        The turn waits on the future the panel resolves.
        """
        if not questions:
            return []
        self._hide_composer()
        self.notify_attention(Attention.QUESTION)
        try:
            answers = await self.query_one(QuestionPanel).ask(questions)
        finally:
            self._show_composer()
        return answers

    def _hide_composer(self) -> None:
        """Takes the composer off screen: the question is what the keys are for."""
        with suppress(NoMatches):
            self.query_one(PromptInput).display = False

    def _show_composer(self) -> None:
        """Restores it, in the order the focus needs: visible first, then focused."""
        with suppress(NoMatches):
            self.query_one(PromptInput).display = True
        self._restore_prompt_focus()

    def _restore_prompt_focus(self) -> None:
        """Gives the keys back to the prompt when the panel releases them.

        While a turn runs the prompt is disabled and the turn itself takes care
        of the focus when it ends, so this only applies when it can be focused.

        It tolerates the composer being gone: this runs from the `finally` of
        `ask_question`, and a question answered — or cancelled — by the app going
        away unmounts the panel and the composer in the same breath.
        """
        with suppress(NoMatches):
            prompt = self.query_one(PromptInput)
            if prompt.disabled:
                return
            prompt.focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Keeps the question panel's keys on the panel while a question is open.

        Reading the conversation or clicking the panel's frame is not an answer,
        and it used to be a silent one: the list stayed on screen with its
        highlight while the arrows scrolled the panel and `Enter` did nothing.

        One redirect, not a fight: the second event lands on the field the panel
        asked for, and `holds_keys` accepts it. With no question open this costs
        two queries and returns.
        """
        panel = self._pending_question()
        if panel is None:
            return
        if panel.holds_keys(event.widget):
            return
        panel.focus_field()

    def _pending_question(self) -> QuestionPanel | None:
        """The panel asking for an answer right now, or `None`.

        A panel on a screen that is not the current one is not asking: a modal
        opened on top of it is, and it owns the keys until it closes.
        """
        panel = self._question_panel()
        if panel is None or not panel.display or panel.screen is not self.screen:
            return None
        return panel

    def _question_panel(self) -> QuestionPanel | None:
        """The panel, or `None` in an app composed without one (the tests build some)."""
        with suppress(NoMatches):
            return self.query_one(QuestionPanel)
        return None

    # --- model information -------------------------------------------------

    def load_model_window(self) -> None:
        """Publishes the active model's context window to the meter.

        The window comes from the catalogue entry that built the model, so there
        is one source of truth: the profile the provider was built with. It only
        sets state; the caller repaints the panel.

        An attached terminal has no agent to ask, and it must not reach for one: the
        window it shows is the one the service projects in its state, applied by
        `_apply_service_state`. Here it stays with whatever the meter already has.
        """
        if self._agent is None:
            return
        window = getattr(getattr(self._agent, 'model', None), 'context_window', None)
        snapshot = self._model_snapshot()
        self._meter.set_window(window if window else snapshot.window)

    @work(exclusive=True)
    async def resolve_model_capabilities(self) -> None:
        """Fetches the catalogue once on startup, if nothing has it cached.

        A turn needs the window and the effort levels; making the user wait for the
        first turn (or open `/model`) to learn them is what left the panel on `?`
        and the effort picker empty right after a start.
        """
        service = getattr(self, '_model_service', None)
        if service is None or not service.is_configured():
            return
        try:
            snapshot = await service.refresh_capability()
        except Exception:  # noqa: BLE001 - an offline start is not an error
            return
        self._meter.set_window(snapshot.window)
        self.refresh_sidebar()
