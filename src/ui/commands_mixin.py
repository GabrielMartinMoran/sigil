"""Comandos de barra: sesión, sidebar, MCP, contexto y ayuda."""

from __future__ import annotations

from textual.widgets import Input

from src.engine.context.meter.render import render_report
from src.ui.help_text import HELP_TEXT
from src.ui.mcp_report import format_mcp_status
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.session_picker import SessionPicker


class CommandsMixin:
    """Despacha los comandos de barra y gestiona el cambio de sesión.

    Espera que la app defina ``_store``, ``_session``, ``_history``,
    ``_scope``, ``_loaded_skills``, ``_mcp_manager``, ``_meter``,
    ``_context_capability``, ``_workspace`` y ``_roadmap_store``.
    """

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter en el prompt: comando de barra o turno del agente."""
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
            self.run_worker(self.handle_command(name, args))
            return

        await self.query_one(ChatLog).add_user(text)
        self.run_turn(text)

    async def handle_command(self, name: str, args: str = '') -> None:
        """Ejecuta el comando pedido."""
        chat = self.query_one(ChatLog)

        handlers = {
            '/new': lambda: self.new_session(chat),
            '/load': lambda: self.load_session(chat),
            '/sidebar': lambda: self.handle_sidebar(args, chat),
            '/mcp': lambda: self.show_mcp(chat),
            '/context': lambda: self.show_context(chat),
            '/debug': lambda: self.handle_debug(args, chat),
            '/help': lambda: chat.add_notice(HELP_TEXT, style='cyan'),
            '/quit': lambda: self.action_quit(),
        }
        handler = handlers.get(name)
        if handler is None:
            await chat.add_notice(f'Unknown command: {name}', style='red')
            return
        await handler()

    def action_quit(self) -> None:
        """Cierra la aplicación, persistiendo la sesión."""
        self.exit()

    async def new_session(self, chat: ChatLog) -> None:
        """Arranca una sesión vacía y limpia el estado de la UI."""
        self.persist()
        self.bind_session(self._store.create_session())
        if self._scope is not None:
            self._scope.reset()
        if self._loaded_skills is not None:
            self._loaded_skills.reset()
        self.refresh_sidebar()
        await chat.clear()
        await chat.add_notice(f'New session {self._session.short_id}.', style='green')
        self.update_status()

    async def load_session(self, chat: ChatLog) -> None:
        """Carga una sesión elegida por el usuario."""
        sessions = self._store.list_sessions()
        if not sessions:
            await chat.add_notice('No saved sessions.', style='yellow')
            return
        chosen = await self.push_screen_wait(SessionPicker(sessions))
        if not chosen:
            return
        self.persist()
        loaded = self._store.load(chosen)
        if loaded is None:
            return
        self.bind_session(loaded)
        if self._scope is not None:
            self._scope.reset()
        if self._loaded_skills is not None:
            self._loaded_skills.reset()
        self.refresh_sidebar()
        await chat.clear()
        await chat.render_history(loaded.messages)
        chat.finalize()
        await chat.add_notice(f'Loaded session {loaded.short_id}.', style='green')
        self.update_status()

    def bind_session(self, session: object) -> None:
        """Apunta la app a ``session``: historial, roadmap y persistencia."""
        self._session = session
        self._history = session.messages
        self.bind_roadmap()

    def bind_roadmap(self) -> None:
        """El roadmap sigue a la sesión activa: cada sesión tiene el suyo."""
        if self._roadmap_store is not None:
            self._roadmap_store.bind_session(self._session.id)

    def persist(self) -> None:
        """Guarda la sesión actual si tiene mensajes."""
        if self._session.messages:
            self._store.save(self._session)

    async def handle_sidebar(self, args: str, chat: ChatLog) -> None:
        """`/sidebar` alterna; `/sidebar on|off` fija el estado."""
        from src.ui.widgets.roadmap_panel import RoadmapPanel

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

    async def handle_debug(self, args: str, chat: ChatLog) -> None:
        """`/debug` alterna la vista; `/debug on|off` fija el estado; `--text` vuelca el reporte.

        La vista reemplaza el chat, así que `--text` es la única forma de leer
        el reporte dentro del propio chat (y de copiarlo desde ahí).
        """
        value = args.strip().lower()
        if value in {'text', '--text'}:
            await chat.add_notice(await self.show_debug_text(), style='white')
            return
        if value in {'', 'toggle'}:
            self.toggle_debug()
        elif value in {'on', 'show'}:
            self.set_debug_visible(True)
        elif value in {'off', 'hide'}:
            self.set_debug_visible(False)
        else:
            await chat.add_notice('Usage: /debug [on|off|--text]', style='yellow')
            return
        await chat.add_notice(f'Debug view {"shown" if self.debug_visible else "hidden"}.', style='cyan')

    async def show_context(self, chat: ChatLog) -> None:
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

    async def show_mcp(self, chat: ChatLog) -> None:
        """Estado de los servidores MCP configurados."""
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
        self.apply_mcp_status(statuses)
