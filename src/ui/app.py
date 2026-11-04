"""UI de terminal de Sigil construida con Textual."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessEventStream
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    ModelSettingsDeltaPart,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from textual import work
from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Input

from src.config_provider import ConfigProvider
from src.engine.agents.model_info import fetch_model_info
from src.engine.agents.retry import RetryNotice, RetryTransientModelErrors
from src.engine.agents.step_limiter import StepLimiter, StepLimitExceeded
from src.engine.commands import COMMANDS, match_commands
from src.engine.context import (
    ContextMeter,
    ContextViewCapability,
    ScopeTracker,
    WorkspaceInstructions,
    render_report,
)
from src.engine.mcp.manager import MCPManager
from src.engine.roadmap.store import RoadmapStore
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.skills.loaded import LoadedSkills
from src.engine.tools.interaction import QuestionAnswer, QuestionHub, QuestionItem
from src.engine.tools.tool_registry import ToolLoggerHub
from src.ui.mcp_report import format_mcp_status
from src.ui.tool_logging import ChatToolLogSink, ToolLogMessage
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_screen import QuestionScreen
from src.ui.widgets.roadmap_panel import RoadmapPanel
from src.ui.widgets.session_picker import SessionPicker
from src.ui.widgets.sidebar_panel import MCPServerState, SidebarPanel
from src.ui.widgets.status_bar import StatusBar

_EFFORTS = {'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
CHECKPOINT_INTERVAL = 1.0
"""Segundos mínimos entre checkpoints durante un turno (el guardado final es obligatorio)."""
_HELP = 'Commands: ' + ', '.join(f'{command.name} — {command.description}' for command in COMMANDS)


class RoadmapChanged(Message):
    pass


class ModelRetryNotice(Message):
    """Aviso de que un fallo transitorio del modelo dispara un reintento."""

    def __init__(self, notice: RetryNotice) -> None:
        super().__init__()
        self.notice = notice


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
        self._history: list[ModelMessage] = list(session.messages)
        self._context_length: int | None = None
        self.menu_visible = False
        # Run en curso: lo usa `_checkpoint` para persistir el avance del turno.
        self._current_run: Any = None
        self._last_checkpoint_at = 0.0

    # --- composición -----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                Vertical(
                    SidebarPanel(id='sidebar-panel'),
                    RoadmapPanel(id='roadmap'),
                    id='sidebar',
                ),
                ChatLog(id='chat'),
                id='main',
            ),
            CommandMenu(id='menu'),
            PromptInput(placeholder='Type your message…  (/ for commands)', id='prompt'),
            StatusBar(id='status'),
            id='root',
        )

    async def on_mount(self) -> None:
        if self._tool_logger is not None:
            self._tool_log_sink = ChatToolLogSink(self)
            self._tool_logger.add_sink(self._tool_log_sink)
        if self._question_hub is not None:
            self._question_hub.set_asker(self._ask_question)
        if self._model_retry is not None:
            self._model_retry.on_retry = self._notify_model_retry
        if self._roadmap_store is not None:
            self._roadmap_store.add_listener(self._handle_roadmap_store_change)
            self.query_one(RoadmapPanel).set_store(self._roadmap_store)
        self.query_one(CommandMenu).display = False
        self._update_status()
        self._refresh_sidebar()
        self._refresh_mcp_status()
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
        elif event.phase == 'success' and event.tool_name == 'load_skill':
            # El skill recién cargado aparece en el sidebar sin esperar al turno.
            self._refresh_sidebar()

    async def on_model_retry_notice(self, message: ModelRetryNotice) -> None:
        notice = message.notice
        await self.query_one(ChatLog).add_notice(
            f'↻ Transient model failure ({notice.reason}); '
            f'retry {notice.next_attempt}/{notice.max_attempts} in {notice.delay:.1f}s',
            style='yellow',
        )

    def _notify_model_retry(self, notice: RetryNotice) -> None:
        """Callback de la capability de reintentos: el aviso lo pinta el handler."""
        self.post_message(ModelRetryNotice(notice))

    async def on_unmount(self) -> None:
        if self._model_retry is not None:
            self._model_retry.on_retry = None
        if self._tool_logger is not None and self._tool_log_sink is not None:
            self._tool_logger.remove_sink(self._tool_log_sink)
            self._tool_log_sink = None
        if self._question_hub is not None:
            self._question_hub.set_asker(None)
        if self._roadmap_store is not None:
            self._roadmap_store.remove_listener(self._handle_roadmap_store_change)

    def _handle_roadmap_store_change(self) -> None:
        """Listener del store. No debe llamarse ``_on_*``: Textual trata ese
        prefijo como handler privado del mensaje y re-postearía en loop."""
        self.post_message(RoadmapChanged())

    def on_roadmap_changed(self, message: RoadmapChanged) -> None:
        self.query_one(RoadmapPanel).refresh_from_store()

    async def _ask_question(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        if not questions:
            return []
        future: asyncio.Future[list[QuestionAnswer] | None] = asyncio.get_running_loop().create_future()

        def _done(result: list[QuestionAnswer] | None) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(QuestionScreen(questions), _done)
        return await future

    @work
    async def _load_model_info(self) -> None:
        # pydantic-ai resuelve la ventana desde genai-prices. El fetch a
        # OpenRouter queda como fallback cuando el perfil no la trae.
        window = getattr(self._agent.model, 'context_window', None)
        if not window:
            info = await fetch_model_info(ConfigProvider.LLM_MODEL)
            window = info.context_length if info is not None else None
        if window:
            self._context_length = window
            self._meter.set_window(window)
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

    def _update_status(self, messages: Sequence[ModelMessage] | None = None) -> None:
        """La barra muestra el **proyectado**: lo que va a pesar el próximo request.

        El medido describe el request anterior; el proyectado suma lo agregado
        desde la última respuesta (tool results, prompt nuevo). Durante un turno
        se le pasa el historial del run, que va por delante de `self._history`.
        """
        snapshot = self._meter.snapshot(self._history if messages is None else messages)
        try:
            bar = self.query_one(StatusBar)
        except NoMatches, ScreenStackError:
            # La app puede no estar montada (tests, arranque). El cálculo no
            # depende de la UI, así que se descarta sólo el pintado.
            return
        bar.update_info(
            provider='openrouter',
            model=ConfigProvider.LLM_MODEL,
            effort=self._effort_in_effect(),
            percent=snapshot.projected_percent,
            context_length=snapshot.window,
        )

    # --- sidebar -----------------------------------------------------------

    def _refresh_sidebar(self) -> None:
        """Skills cargadas en la sesión, arriba del roadmap."""
        try:
            panel = self.query_one(SidebarPanel)
        except NoMatches, ScreenStackError:
            return
        names = self._loaded_skills.names() if self._loaded_skills is not None else ()
        panel.update_skills(names)

    @work(exclusive=True)
    async def _refresh_mcp_status(self) -> None:
        """Conteo real de servidores MCP: conecta y actualiza el panel.

        Corre en un worker para no bloquear el arranque: levantar los servidores
        puede tardar. Si falla, el panel se queda con lo que ya sabía.
        """
        if self._mcp_manager is None or not self._mcp_manager:
            return
        try:
            statuses = await self._mcp_manager.describe(connect=True)
        except Exception:  # noqa: BLE001 - el diagnóstico nunca rompe la app
            return
        servers = [MCPServerState(name=status.name, ok=not status.error) for status in statuses if status.enabled]
        try:
            self.query_one(SidebarPanel).update_mcp(servers)
        except NoMatches, ScreenStackError:
            return

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
            name, _, args = text.partition(' ')
            self.run_worker(self._handle_command(name, args))
            return

        await self.query_one(ChatLog).add_user(text)
        self._run_turn(text)

    async def _handle_command(self, name: str, args: str = '') -> None:
        chat = self.query_one(ChatLog)

        if name == '/new':
            self._persist()
            self._session = self._store.create_session()
            self._history = self._session.messages
            self._bind_roadmap()
            if self._scope is not None:
                self._scope.reset()
            if self._loaded_skills is not None:
                self._loaded_skills.reset()
            self._refresh_sidebar()
            await chat.clear()
            await chat.add_notice(f'New session {self._session.short_id}.', style='green')
            self._update_status()
        elif name == '/load':
            sessions = self._store.list_sessions()
            if not sessions:
                await chat.add_notice('No saved sessions.', style='yellow')
                return
            chosen = await self.push_screen_wait(SessionPicker(sessions))
            if chosen:
                self._persist()
                loaded = self._store.load(chosen)
                if loaded is not None:
                    self._session = loaded
                    self._history = loaded.messages
                    self._bind_roadmap()
                    if self._scope is not None:
                        self._scope.reset()
                    if self._loaded_skills is not None:
                        self._loaded_skills.reset()
                    self._refresh_sidebar()
                    await chat.clear()
                    await chat.render_history(loaded.messages)
                    chat.finalize()
                    await chat.add_notice(f'Loaded session {loaded.short_id}.', style='green')
                    self._update_status()
        elif name == '/sidebar':
            await self._handle_sidebar(args, chat)
        elif name == '/mcp':
            await self._show_mcp(chat)
        elif name == '/context':
            await self._show_context(chat)
        elif name == '/help':
            await chat.add_notice(_HELP, style='cyan')
        elif name == '/quit':
            self.exit()
        else:
            await chat.add_notice(f'Unknown command: {name}', style='red')

    async def _handle_sidebar(self, args: str, chat: ChatLog) -> None:
        """`/sidebar` alterna; `/sidebar on|off` fija el estado."""
        value = args.strip().lower()
        panel = self.query_one(RoadmapPanel)
        if value in {'', 'toggle'}:
            panel.set_shown(not panel.shown)
        elif value in {'on', 'show'}:
            panel.set_shown(True)
        elif value in {'off', 'hide'}:
            panel.set_shown(False)
        else:
            await chat.add_notice('Usage: /sidebar [on|off]', style='yellow')
            return
        await chat.add_notice(f'Sidebar {"shown" if panel.shown else "hidden"}.', style='cyan')

    async def _show_context(self, chat: ChatLog) -> None:
        """Desglose de contexto: medido, proyectado, presupuesto y turnos."""
        view = self._context_capability.last_view if self._context_capability is not None else None
        report = self._meter.report(self._history, view=view)
        lines = [render_report(report)]

        if self._scope is not None:
            scopes = self._scope.relative_scopes()
            lines.append('')
            lines.append('Active scope')
            lines.append(f'  directories      : {", ".join(scopes)}')
            if self._workspace is not None:
                files = self._workspace.describe(self._scope.scopes())
                lines.append(f'  instructions     : {", ".join(files) if files else "(none)"}')

        await chat.add_notice('\n'.join(lines), style='white')

    async def _show_mcp(self, chat: ChatLog) -> None:
        if self._mcp_manager is None or not self._mcp_manager:
            await chat.add_notice(
                'No MCP servers configured. Create .sigil/config.json with an `mcp_servers` section.',
                style='yellow',
            )
            return

        await chat.add_notice('Querying MCP servers…', style='cyan')
        statuses = await self._mcp_manager.describe(connect=True)
        await chat.add_notice(format_mcp_status(statuses), style='white')
        # `/mcp` es el diagnóstico: el panel se actualiza con lo que acaba de ver.
        servers = [MCPServerState(name=status.name, ok=not status.error) for status in statuses if status.enabled]
        self.query_one(SidebarPanel).update_mcp(servers)

    def _persist(self) -> None:
        if self._session.messages:
            self._store.save(self._session)

    def _bind_roadmap(self) -> None:
        """El roadmap sigue a la sesión activa: cada sesión tiene el suyo."""
        if self._roadmap_store is not None:
            self._roadmap_store.bind_session(self._session.id)

    # --- turno del agente -------------------------------------------------

    def _turn_handler(self, limiter: StepLimiter, chat: ChatLog):
        async def handler(_ctx: Any, events: Any) -> None:
            async for event in events:
                limiter.count(event)
                # Checkpoint por evento: apenas el run agrega un mensaje al
                # historial (p. ej. el prompt del usuario) queda en disco.
                self._checkpoint()
                await self._dispatch_event(event, chat)

        return handler

    def _checkpoint(self, *, force: bool = False) -> None:
        """Persiste el avance del turno en curso, como mucho una vez por segundo.

        Se llama después de cada iteración del agente y de cada evento del
        stream. Si el proceso muere a mitad de un turno largo, la sesión en
        disco conserva lo completado hasta el último checkpoint. Sólo escribe
        cuando el historial cambió y pasó la ventana de throttle; el guardado
        final del turno es obligatorio y no pasa por acá.
        """
        run = self._current_run
        if run is None:
            return
        messages = run.all_messages()
        if not messages:
            return
        current = self._session.messages
        if len(messages) == len(current) and current and messages[-1] is current[-1]:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint_at < CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint_at = now
        self._session.messages = list(messages)
        self._store.save(self._session)
        # La barra se actualiza por paso, no sólo al cerrar el turno.
        self._update_status(messages)

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
        handler = self._turn_handler(limiter, chat)
        prompt.disabled = True
        # `agent.iter` (en vez de `agent.run`) conserva el run accesible: si el
        # turno se aborta por el límite de pasos o por un error, `run.all_messages()`
        # devuelve lo que se alcanzó a completar y la sesión no pierde el contexto.
        run: Any = None
        history = self._history
        # El primer checkpoint del turno (el prompt del usuario) se guarda ya.
        self._last_checkpoint_at = 0.0
        try:
            async with self._agent.iter(
                text,
                message_history=self._history,
                capabilities=[ProcessEventStream(handler=handler)],
                model_settings=self._model_settings(),
            ) as run:
                self._current_run = run
                async for _node in run:
                    # Checkpoint por iteración: cada nodo completado (respuesta
                    # del modelo, tanda de tools) queda persistido.
                    self._checkpoint()
            history = run.all_messages()
        except StepLimitExceeded:
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(
                f'Step limit of {ConfigProvider.MAX_STEPS} reached; turn aborted.',
                style='bold red',
            )
        except Exception as exc:  # noqa: BLE001
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(f'Error: {exc}', style='bold red')
        finally:
            self._current_run = None
            prompt.disabled = False
            prompt.focus()
            chat.finalize()

        self._history = history
        self._session.messages = self._history
        self._store.save(self._session)
        self._update_status()
