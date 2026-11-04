"""Sigil terminal UI built with Textual.

The app is the UI composition point: it builds the widget tree and delegates
each responsibility to a mixin with its own state.

- `StatusMixin`    : status bar (model, effort, projected context).
- `SidebarMixin`   : side panel: loaded skills and MCP servers.
- `MenuMixin`      : command menu that appears when typing `/`.
- `CommandsMixin`  : slash commands and session switching.
- `DebugMixin`     : agent debug view (`alt+d`), which replaces the chat.
- `TurnMixin`      : turn execution and on-disk checkpoints.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic_ai import Agent
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical

from src.config_provider import ConfigProvider
from src.engine.agents.model_info import fetch_model_info
from src.engine.agents.retry import RetryNotice, RetryTransientModelErrors
from src.engine.context.capability import ContextViewCapability
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.scope import ScopeTracker
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.mcp.manager.manager import MCPManager
from src.engine.roadmap.store import RoadmapStore
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.skills.loaded import LoadedSkills
from src.engine.tooling.logging import ToolLoggerHub
from src.ui.commands_mixin import CommandsMixin
from src.ui.debug_mixin import DebugMixin
from src.ui.menu_mixin import MenuMixin
from src.ui.messages import ModelRetryNotice, RoadmapChanged
from src.ui.sidebar_mixin import SidebarMixin
from src.ui.status_mixin import StatusMixin
from src.ui.tool_logging import ChatToolLogSink, ToolLogMessage
from src.ui.turn_mixin import TurnMixin
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_screen import QuestionScreen
from src.ui.widgets.roadmap_panel import RoadmapPanel
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar


class SigilApp(
    StatusMixin,
    SidebarMixin,
    MenuMixin,
    CommandsMixin,
    DebugMixin,
    TurnMixin,
    App,
):
    """Terminal chat: scrollable history, fixed input, menu and status bar."""

    CSS_PATH = 'theme.tcss'
    TITLE = 'Sigil'

    # Focus lives in the input, which swallows the keys: the shortcut needs
    # `priority` to reach the app. `escape` is handled by `PromptInput`, which
    # already uses it for the command menu.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('alt+d', 'toggle_debug', 'Debug view', priority=True, show=False),
    ]

    def __init__(
        self,
        agent: Agent,
        store: SessionStore,
        session: Session,
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
        self._agent = agent
        self._store = store
        self._session = session
        self._tool_logger = tool_logger
        self._mcp_manager = mcp_manager
        self._startup_notices = tuple(startup_notices)
        self._question_hub = question_hub
        self._roadmap_store = roadmap_store
        self._model_retry = model_retry
        self._meter = meter or ContextMeter(output_reserve=ConfigProvider.CONTEXT_OUTPUT_RESERVE)
        self._context_capability = context_capability
        self._scope = scope
        self._workspace = workspace
        self._loaded_skills = loaded_skills
        self._tool_log_sink: ChatToolLogSink | None = None
        self._history: list[Any] = list(session.messages)
        self._context_length: int | None = None
        self.menu_visible = False
        # Debug view: it replaces the chat, it does not coexist with it.
        self.debug_visible = False
        self._debug_timer: Any = None
        self._debug_pending: list[Any] | None = None
        # Run in progress: `checkpoint` uses it to persist turn progress.
        self._current_run: Any = None
        self._last_checkpoint_at = 0.0

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                Vertical(
                    SidebarPanel(id='sidebar-panel'),
                    RoadmapPanel(id='roadmap'),
                    id='sidebar',
                ),
                ChatLog(id='chat'),
                AgentDebugView(id='debug'),
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
        self.update_status()
        self.refresh_sidebar()
        self.refresh_mcp_status()
        for notice in self._startup_notices:
            await self.query_one(ChatLog).add_notice(notice, style='yellow')
        if self._history:
            chat = self.query_one(ChatLog)
            await chat.render_history(self._history)
            chat.finalize()
        self.query_one(PromptInput).focus()
        self.load_model_info()

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
        """Opens the question screen and waits for the user's answer."""
        if not questions:
            return []
        future: asyncio.Future[list[QuestionAnswer] | None] = asyncio.get_running_loop().create_future()

        def _done(result: list[QuestionAnswer] | None) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(QuestionScreen(questions), _done)
        return await future

    # --- model information -------------------------------------------------

    @work
    async def load_model_info(self) -> None:
        """Resolves the model context window and publishes it to the bar."""
        # pydantic-ai resolves the window from genai-prices. The OpenRouter
        # fetch remains as a fallback when the profile does not carry it.
        window = getattr(self._agent.model, 'context_window', None)
        if not window:
            info = await fetch_model_info(ConfigProvider.LLM_MODEL)
            window = info.context_length if info is not None else None
        if window:
            self._context_length = window
            self._meter.set_window(window)
        self.update_status()
