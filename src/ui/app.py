"""Sigil terminal UI built with Textual.

The app is the UI composition point: it builds the widget tree and delegates
each responsibility to a mixin with its own state.

- `ModelViewMixin` : the active model and effort in effect, read by the chrome.
- `SidebarMixin`   : side panel: active model, context, skills and MCP servers.
- `MenuMixin`      : command menu that appears when typing `/`.
- `CommandsMixin`  : slash commands and session switching.
- `ProviderMixin`  : `/provider`, `/model`, `/effort` and their screens.
- `DebugMixin`     : agent debug view (`alt+d`), which replaces the chat.
- `TurnMixin`      : turn execution and on-disk checkpoints.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic_ai import Agent
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical

from src.engine.agents.retry import RetryNotice, RetryTransientModelErrors
from src.engine.context.capability import ContextViewCapability
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.scope import ScopeTracker
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.mcp.manager.manager import MCPManager
from src.engine.providers.service import ModelService
from src.engine.roadmap.store import RoadmapStore
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.settings import SigilSettings, load_settings
from src.engine.skills.loaded import LoadedSkills
from src.engine.tooling.logging import ToolLoggerHub
from src.ui.commands_mixin import CommandsMixin
from src.ui.debug_mixin import DebugMixin
from src.ui.menu_mixin import MenuMixin
from src.ui.messages import ChatNotice, ModelRetryNotice, RoadmapChanged
from src.ui.model_view_mixin import ModelViewMixin
from src.ui.provider_mixin import ProviderMixin
from src.ui.sidebar_mixin import SidebarMixin
from src.ui.tool_logging import ChatToolLogSink, ToolLogMessage
from src.ui.turn_mixin import TurnMixin
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel
from src.ui.widgets.roadmap_panel import RoadmapPanel
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar


class SigilApp(
    ModelViewMixin,
    SidebarMixin,
    MenuMixin,
    CommandsMixin,
    ProviderMixin,
    DebugMixin,
    TurnMixin,
    App,
):
    """Terminal chat: scrollable history, fixed input, menu and side panel."""

    CSS_PATH = 'theme.tcss'
    TITLE = 'Sigil'

    # Focus lives in the input, which swallows the keys. The pickers therefore
    # read `ctrl+m` / `ctrl+t` in `PromptInput.on_key` instead of here: a global
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
                Vertical(
                    SidebarPanel(id='sidebar-panel'),
                    RoadmapPanel(id='roadmap'),
                    id='sidebar',
                ),
                Vertical(
                    ChatLog(id='chat'),
                    AgentDebugView(id='debug'),
                    QuestionPanel(id='questions'),
                    id='conversation',
                ),
                id='main',
            ),
            CommandMenu(id='menu'),
            PromptInput(placeholder='Type your message…  (/ for commands)', id='prompt'),
            StatusBar(id='status'),
            id='root',
        )

    # --- lifecycle ---------------------------------------------------------

    async def on_mount(self) -> None:
        if self._tool_logger is not None:
            self._tool_log_sink = ChatToolLogSink(self)
            self._tool_logger.add_sink(self._tool_log_sink)
        if self._question_hub is not None:
            self._question_hub.set_asker(self.ask_question)
        if self._model_retry is not None:
            self._model_retry.on_retry = self.notify_model_retry
        if self._roadmap_store is not None:
            self._roadmap_store.add_listener(self.handle_roadmap_store_change)
            self.query_one(RoadmapPanel).set_store(self._roadmap_store)
        self.query_one(CommandMenu).display = False
        self.refresh_sidebar()
        self.refresh_mcp_status()
        for notice in self._startup_notices:
            await self.query_one(ChatLog).add_notice(notice, style='yellow')
        if self._history:
            chat = self.query_one(ChatLog)
            await chat.render_history(self._history)
            chat.finalize()
        self.query_one(PromptInput).focus()
        self.load_model_window()
        self.resolve_model_capabilities()

    async def on_unmount(self) -> None:
        if self._model_retry is not None:
            self._model_retry.on_retry = None
        if self._tool_logger is not None and self._tool_log_sink is not None:
            self._tool_logger.remove_sink(self._tool_log_sink)
            self._tool_log_sink = None
        if self._question_hub is not None:
            self._question_hub.set_asker(None)
        if self._roadmap_store is not None:
            self._roadmap_store.remove_listener(self.handle_roadmap_store_change)

    # --- tool and model events ---------------------------------------------

    async def on_chat_notice(self, message: ChatNotice) -> None:
        """Appends a notice posted from a synchronous context."""
        await self.query_one(ChatLog).add_notice(message.text, style=message.style)

    async def on_tool_log_message(self, message: ToolLogMessage) -> None:
        event = message.event
        chat = self.query_one(ChatLog)
        if event.phase == 'start':
            await chat.add_tool_call(event.tool_name, event.args)
        elif event.phase == 'error':
            await chat.add_notice(f'✖ {event.tool_name}: {event.error}', style='bold red')
        elif event.phase == 'success' and event.tool_name == 'load_skill':
            # The freshly loaded skill shows up in the sidebar without waiting for the turn.
            self.refresh_sidebar()
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
        self.query_one(RoadmapPanel).refresh_from_store()

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
