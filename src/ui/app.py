"""Sigil terminal UI built with Textual.

The app is the UI composition point: it builds the widget tree and delegates
each responsibility to a mixin with its own state.

- `ModelViewMixin` : the active model and effort in effect, read by the chrome.
- `AgentMixin`     : `/agent`, the `ctrl+a` picker and the agent in the status bar.
- `SidebarMixin`   : side panel: active model, context budget, skills, MCP servers.
- `MenuMixin`      : command menu that appears when typing `/`.
- `CommandsMixin`  : slash commands and session switching.
- `ProviderMixin`  : `/provider`, `/model`, `/effort` and their screens.
- `ThemeMixin`     : `/theme` and `/reasoning-visibility`.
- `ActivityMixin`  : the status bar: spinner, workspace and diff counters.
- `DebugMixin`     : agent debug view (`alt+d`), which replaces the chat.
- `TurnMixin`      : turn execution and on-disk checkpoints.
"""

from __future__ import annotations

from collections.abc import Sequence
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
from src.engine.context.capability import ContextViewCapability
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.scope import ScopeTracker
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.mcp.manager.manager import MCPManager
from src.engine.preferences import Preferences, PreferencesStore
from src.engine.providers.service import ModelService
from src.engine.roadmap.store import RoadmapStore
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.settings import SigilSettings, load_settings
from src.engine.skills.loaded import LoadedSkills
from src.engine.themes import ThemeRegistry
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.workspace.manager import WorkspaceManager
from src.ui.activity_mixin import ActivityMixin
from src.ui.agent_mixin import AgentMixin
from src.ui.commands_mixin import CommandsMixin
from src.ui.debug_mixin import DebugMixin
from src.ui.menu_mixin import MenuMixin
from src.ui.messages import ChatNotice, ModelRetryNotice, RoadmapChanged
from src.ui.model_view_mixin import ModelViewMixin
from src.ui.provider_mixin import ProviderMixin
from src.ui.sidebar_mixin import SidebarMixin
from src.ui.theme_mixin import ThemeMixin
from src.ui.tool_logging import ChatToolLogSink, ToolLogMessage
from src.ui.turn_mixin import TurnMixin
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar
from src.ui.workspace_mixin import WorkspaceMixin


class SigilApp(
    ModelViewMixin,
    AgentMixin,
    SidebarMixin,
    MenuMixin,
    CommandsMixin,
    ProviderMixin,
    ThemeMixin,
    WorkspaceMixin,
    ActivityMixin,
    DebugMixin,
    TurnMixin,
    App,
):
    """Terminal chat: scrollable history, fixed input, menu and side panel."""

    CSS_PATH = 'theme.tcss'
    TITLE = 'Sigil'

    # Focus lives in the input, which swallows the keys. The pickers therefore
    # read `ctrl+m` / `ctrl+t` in `PromptInput._on_key` instead of here: a global
    # binding would claim Enter on terminals that cannot tell it from Ctrl+M.
    #
    # There is no global `escape` binding on purpose: the focused widget sees the
    # key first, and the abort has to coexist with the `escape` that cancels a
    # question panel. The prompt handles it (see `PromptInput`).
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('alt+d', 'toggle_debug', 'Debug view', priority=True, show=False),
    ]

    def __init__(
        self,
        agent: Agent,
        store: SessionStore,
        session: Session,
        settings: SigilSettings | None = None,
        model_service: ModelService | None = None,
        login_flow: Any = None,
        tool_logger: ToolLoggerHub | None = None,
        mcp_manager: MCPManager | None = None,
        startup_notices: Sequence[str] = (),
        question_hub: QuestionHub | None = None,
        roadmap_store: RoadmapStore | None = None,
        model_retry: RetryTransientModelErrors | None = None,
        meter: ContextMeter | None = None,
        context_capability: ContextViewCapability | None = None,
        scope: ScopeTracker | None = None,
        workspace: WorkspaceInstructions | None = None,
        loaded_skills: LoadedSkills | None = None,
        workspace_manager: WorkspaceManager | None = None,
        preferences: Preferences | None = None,
        preferences_store: PreferencesStore | None = None,
        themes: ThemeRegistry | None = None,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings or load_settings({})
        self._agent = agent
        self._store = store
        self._session = session
        self._model_service = model_service
        self._login_flow = login_flow
        self._tool_logger = tool_logger
        self._mcp_manager = mcp_manager
        self._startup_notices = tuple(startup_notices)
        self._question_hub = question_hub
        self._roadmap_store = roadmap_store
        self._model_retry = model_retry
        self._meter = meter or ContextMeter(output_reserve=self._settings.output_reserve)
        self._context_capability = context_capability
        self._scope = scope
        self._workspace = workspace
        self._loaded_skills = loaded_skills
        self._workspace_manager = workspace_manager
        # Preferences are read here, not in `on_mount`: the reasoning preference
        # decides how the first painted transcript looks.
        self._preferences_store = preferences_store or PreferencesStore()
        self._preferences = preferences or self._preferences_store.load()
        self._themes = themes or ThemeRegistry.load()
        self._agent_registry = agent_registry
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
                    ChatLog(id='chat', reasoning_visible=self._preferences.reasoning_visible),
                    AgentDebugView(id='debug'),
                    QuestionPanel(id='questions'),
                    id='conversation',
                ),
                id='main',
            ),
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
            self.query_one(SidebarPanel).set_store(self._roadmap_store)
        self.query_one(CommandMenu).display = False
        # The session carries the agent it runs with from the first paint, so a
        # conversation saved before any switch still remembers it.
        self.remember_agent(self.active_agent())
        self.refresh_sidebar()
        self.refresh_mcp_status()
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
            await chat.render_history(self._history)
            chat.finalize()
        self.query_one(PromptInput).focus()
        self.load_model_window()
        self.resolve_model_capabilities()

    async def on_unmount(self) -> None:
        self._stop_diff_polling()
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

    async def on_tool_log_message(self, message: ToolLogMessage) -> None:
        event = message.event
        chat = self.query_one(ChatLog)
        if event.phase == 'start':
            await chat.add_tool_call(event.tool_name, event.args)
            self.set_activity_label(f'Running {event.tool_name}')
        elif event.phase == 'error':
            await chat.add_tool_error(event.tool_name, event.error or 'failed')
        elif event.phase == 'success' and event.tool_name == 'load_skill':
            # The freshly loaded skill shows up in the sidebar without waiting for the turn.
            self.refresh_sidebar()
        # A tool call is the moment the tree can change: the bar catches up now
        # instead of on the next poll.
        if event.phase != 'start':
            self.refresh_diff()
        # The debug view follows the turn, just like the chat.
        self.refresh_debug()

    # --- debug view -------------------------------------------------------

    def action_toggle_debug(self) -> None:
        """`alt+d`: replaces the chat with the agent debug view."""
        self.toggle_debug()

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

        It is a `key_escape` method and not a binding on purpose: a non-priority
        binding would show up in the help as a second way to do the same thing,
        and a priority one would run before the question panel and swallow the
        `Esc` that cancels a question.
        """
        if self.request_abort():
            self.abort_turn()

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

    # --- interactive questions --------------------------------------------

    async def ask_question(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        """Shows the questions under the chat and waits for the answers.

        It is not a modal screen: the questions are usually about what is in the
        conversation, and that has to stay readable while answering. The panel
        only borrows part of the chat's height, and the turn waits on the future
        the panel resolves.
        """
        answers = await self.query_one(QuestionPanel).ask(questions)
        self._restore_prompt_focus()
        return answers

    def _restore_prompt_focus(self) -> None:
        """Gives the keys back to the prompt when the panel releases them.

        While a turn runs the prompt is disabled and the turn itself takes care
        of the focus when it ends, so this only applies when it can be focused.
        """
        prompt = self.query_one(PromptInput)
        if prompt.disabled:
            return
        prompt.focus()

    # --- model information -------------------------------------------------

    def load_model_window(self) -> None:
        """Publishes the active model's context window to the meter.

        The window comes from the catalogue entry that built the model, so there
        is one source of truth: the profile the provider was built with. It only
        sets state; the caller repaints the panel.
        """
        window = getattr(self._agent.model, 'context_window', None)
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
