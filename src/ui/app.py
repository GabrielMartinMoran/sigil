"""UI de terminal de Sigil construida con Textual."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    ModelResponse,
    ModelSettingsDeltaPart,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input

from src.config_provider import ConfigProvider
from src.engine.agents.model_info import context_usage_percent, fetch_model_info
from src.engine.agents.step_limiter import StepLimiter, StepLimitExceeded
from src.engine.commands import COMMANDS, match_commands
from src.engine.mcp.manager import MCPManager
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.tools.tool_registry import ToolLoggerHub
from src.ui.mcp_report import format_mcp_status
from src.ui.tool_logging import ChatToolLogSink, ToolLogMessage
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.session_picker import SessionPicker
from src.ui.widgets.status_bar import StatusBar

_EFFORTS = {'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
_HELP = 'Comandos: ' + ', '.join(f'{command.name} — {command.description}' for command in COMMANDS)


class SigilApp(App):
    """Chat de terminal: historial scrolleable, input fijo, menú y barra de estado."""

    CSS_PATH = 'theme.tcss'
    TITLE = 'Sigil'

    def __init__(
        self,
        agent: Agent,
        store: SessionStore,
        session: Session,
        tool_logger: ToolLoggerHub | None = None,
        mcp_manager: MCPManager | None = None,
        startup_notices: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self._agent = agent
        self._store = store
        self._session = session
        self._tool_logger = tool_logger
        self._mcp_manager = mcp_manager
        self._startup_notices = tuple(startup_notices)
        self._tool_log_sink: ChatToolLogSink | None = None
        self._history: list[ModelMessage] = list(session.messages)
        self._context_length: int | None = None
        self.menu_visible = False

    # --- composición -----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Vertical(
            ChatLog(id='chat'),
            CommandMenu(id='menu'),
            PromptInput(placeholder='Escribí tu mensaje…  (/ para comandos)', id='prompt'),
            StatusBar(id='status'),
            id='root',
        )

    async def on_mount(self) -> None:
        if self._tool_logger is not None:
            self._tool_log_sink = ChatToolLogSink(self)
            self._tool_logger.add_sink(self._tool_log_sink)
        self.query_one(CommandMenu).display = False
        self._update_status()
        for notice in self._startup_notices:
            await self.query_one(ChatLog).add_notice(notice, style='yellow')
        if self._history:
            chat = self.query_one(ChatLog)
            await chat.render_history(self._history)
            chat.finalize()
        self.query_one(PromptInput).focus()
        self._load_model_info()

    async def on_tool_log_message(self, message: ToolLogMessage) -> None:
        event = message.event
        chat = self.query_one(ChatLog)
        if event.phase == 'start':
            await chat.add_tool_call(event.tool_name, event.args)
        elif event.phase == 'error':
            await chat.add_notice(f'✖ {event.tool_name}: {event.error}', style='bold red')

    async def on_unmount(self) -> None:
        if self._tool_logger is not None and self._tool_log_sink is not None:
            self._tool_logger.remove_sink(self._tool_log_sink)
            self._tool_log_sink = None

    @work
    async def _load_model_info(self) -> None:
        info = await fetch_model_info(ConfigProvider.LLM_MODEL)
        if info is not None and info.context_length:
            self._context_length = info.context_length
        self._update_status()

    # --- barra de estado --------------------------------------------------

    def _effort_in_effect(self) -> str:
        for message in reversed(self._history):
            for part in reversed(getattr(message, 'parts', [])):
                if isinstance(part, ModelSettingsDeltaPart):
                    effort = part.settings.get('thinking')
                    if effort:
                        return str(effort)
        configured = ConfigProvider.REASONING_EFFORT
        return configured if configured in _EFFORTS else 'default'

    def _used_tokens(self) -> int:
        for message in reversed(self._history):
            if isinstance(message, ModelResponse):
                usage = message.usage
                return (usage.input_tokens or 0) + (usage.output_tokens or 0)
        return 0

    def _update_status(self) -> None:
        percent = context_usage_percent(self._used_tokens(), self._context_length)
        self.query_one(StatusBar).update_info(
            provider='openrouter',
            model=ConfigProvider.LLM_MODEL,
            effort=self._effort_in_effect(),
            percent=percent,
            context_length=self._context_length,
        )

    # --- menú de comandos -------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is not self.query_one(PromptInput):
            return

        matches = match_commands(event.value)
        menu = self.query_one(CommandMenu)
        if matches:
            menu.update_matches(matches)
            menu.display = True
            self.menu_visible = True
        else:
            menu.display = False
            self.menu_visible = False

    def menu_move(self, direction: int) -> None:
        if not self.menu_visible:
            return
        menu = self.query_one(CommandMenu)
        if direction < 0:
            menu.action_cursor_up()
        else:
            menu.action_cursor_down()

    def menu_hide(self) -> None:
        self.query_one(CommandMenu).display = False
        self.menu_visible = False

    async def menu_choose(self) -> None:
        menu = self.query_one(CommandMenu)
        option = menu.highlighted_option
        if option is None or option.id is None:
            return
        self.menu_hide()
        self.query_one(PromptInput).value = ''
        # En un worker para poder usar `push_screen_wait` (p. ej. /load).
        self.run_worker(self._handle_command(option.id))

    # --- entrada ----------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self.query_one(PromptInput):
            return

        text = event.value.strip()
        if not text:
            return

        prompt = self.query_one(PromptInput)
        prompt.value = ''
        self.menu_hide()

        if text.startswith('/'):
            self.run_worker(self._handle_command(text.split(' ', 1)[0]))
            return

        await self.query_one(ChatLog).add_user(text)
        self._run_turn(text)

    async def _handle_command(self, name: str) -> None:
        chat = self.query_one(ChatLog)

        if name == '/new':
            self._persist()
            self._session = self._store.create_session()
            self._history = self._session.messages
            await chat.clear()
            await chat.add_notice(f'Nueva sesión {self._session.short_id}.', style='green')
            self._update_status()
        elif name == '/load':
            sessions = self._store.list_sessions()
            if not sessions:
                await chat.add_notice('No hay sesiones guardadas.', style='yellow')
                return
            chosen = await self.push_screen_wait(SessionPicker(sessions))
            if chosen:
                self._persist()
                loaded = self._store.load(chosen)
                if loaded is not None:
                    self._session = loaded
                    self._history = loaded.messages
                    await chat.clear()
                    await chat.render_history(loaded.messages)
                    chat.finalize()
                    await chat.add_notice(f'Cargada sesión {loaded.short_id}.', style='green')
                    self._update_status()
        elif name == '/mcp':
            await self._show_mcp(chat)
        elif name == '/help':
            await chat.add_notice(_HELP, style='cyan')
        elif name == '/quit':
            self.exit()
        else:
            await chat.add_notice(f'Comando desconocido: {name}', style='red')

    async def _show_mcp(self, chat: ChatLog) -> None:
        if self._mcp_manager is None or not self._mcp_manager:
            await chat.add_notice(
                'No hay servidores MCP configurados. Creá .sigil/config.json con una sección `mcp_servers`.',
                style='yellow',
            )
            return

        await chat.add_notice('Consultando servidores MCP…', style='cyan')
        statuses = await self._mcp_manager.describe(connect=True)
        await chat.add_notice(format_mcp_status(statuses), style='white')

    def _persist(self) -> None:
        if self._session.messages:
            self._store.save(self._session)

    # --- turno del agente -------------------------------------------------

    def _turn_handler(self, limiter: StepLimiter, chat: ChatLog):
        async def handler(_ctx: Any, events: Any) -> None:
            async for event in events:
                limiter.count(event)
                await self._dispatch_event(event, chat)

        return handler

    async def _dispatch_event(self, event: Any, chat: ChatLog) -> None:
        if isinstance(event, PartStartEvent):
            if event.index == 0:
                await chat.begin_response()
            part = event.part
            if isinstance(part, ThinkingPart) and part.content:
                await chat.add_thinking(part.content)
            elif isinstance(part, TextPart) and part.content:
                await chat.add_text(part.content)
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and delta.content_delta:
                await chat.add_text(delta.content_delta)
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                await chat.add_thinking(delta.content_delta)
        elif isinstance(event, FunctionToolResultEvent):
            ok = getattr(event.part, 'outcome', 'success') == 'success'
            await chat.add_tool_result(event.part.tool_name, event.part.content, ok=ok)

    def _model_settings(self) -> dict[str, Any] | None:
        effort = ConfigProvider.REASONING_EFFORT
        if effort in _EFFORTS:
            return {'thinking': effort}
        return None

    @work(exclusive=True)
    async def _run_turn(self, text: str) -> None:
        chat = self.query_one(ChatLog)
        prompt = self.query_one(PromptInput)
        limiter = StepLimiter(ConfigProvider.MAX_STEPS)
        prompt.disabled = True
        try:
            result = await self._agent.run(
                text,
                message_history=self._history,
                event_stream_handler=self._turn_handler(limiter, chat),
                model_settings=self._model_settings(),
            )
        except StepLimitExceeded:
            await chat.add_notice(
                f'Se alcanzó el límite de {ConfigProvider.MAX_STEPS} pasos; turno abortado.',
                style='bold red',
            )
            return
        except Exception as exc:  # noqa: BLE001
            await chat.add_notice(f'Error: {exc}', style='bold red')
            return
        finally:
            prompt.disabled = False
            prompt.focus()
            chat.finalize()

        self._history = result.all_messages()
        self._session.messages = self._history
        self._store.save(self._session)
        self._update_status()
