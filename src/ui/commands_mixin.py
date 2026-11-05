"""Slash commands: session, workspace, sidebar, MCP, context, theme and help."""

from __future__ import annotations

from src.engine.context.meter.render import render_report
from src.engine.sessions.catalog import catalog
from src.engine.workspace.arguments import UsageError, parse_new_args
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.ui.help_text import HELP_TEXT, KEYS_TEXT
from src.ui.mcp_report import format_mcp_status
from src.ui.messages import ChatNotice
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.session_picker import SessionPicker


class CommandsMixin:
    """Dispatches the slash commands and manages session switching.

    It expects the app to define ``_store``, ``_session``, ``_history``,
    ``_scope``, ``_loaded_skills``, ``_mcp_manager``, ``_meter``,
    ``_context_capability``, ``_workspace``, ``_roadmap_store`` and
    ``_workspace_manager``.
    """

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Enter on the composer: slash command or agent turn.

        The composer clears itself before posting, so a slow turn cannot leave the
        typed text sitting in the box for a second send.
        """
        text = event.value.strip()
        if not text:
            return

        self.menu_hide()

        if text.startswith('/'):
            name, _, args = text.partition(' ')
            self.run_worker(self.handle_command(name, args))
            return

        await self.query_one(ChatLog).add_user(text)
        self.run_turn(text)

    async def handle_command(self, name: str, args: str = '') -> None:
        """Runs the requested command."""
        chat = self.query_one(ChatLog)

        handlers = {
            '/new': lambda: self.new_session(chat, args),
            '/load': lambda: self.load_session(chat),
            '/provider': lambda: self.show_providers(chat),
            '/model': lambda: self.show_models(chat, args),
            '/effort': lambda: self.show_effort(chat, args),
            '/reasoning-visibility': lambda: self.handle_reasoning_visibility(args, chat),
            '/theme': lambda: self.handle_theme_command(args, chat),
            '/sidebar': lambda: self.handle_sidebar(args, chat),
            '/mcp': lambda: self.show_mcp(chat),
            '/context': lambda: self.show_context(chat),
            '/debug': lambda: self.handle_debug(args, chat),
            '/help': lambda: self.show_help(chat),
            '/quit': lambda: self.action_quit(),
        }
        handler = handlers.get(name)
        if handler is None:
            await chat.add_notice(f'Unknown command: {name}', style='red')
            return
        await handler()

    async def show_help(self, chat: ChatLog) -> None:
        """Commands plus the keys that are not discoverable from the menu."""
        await chat.add_notice(HELP_TEXT, style='cyan')
        await chat.add_notice(KEYS_TEXT, style='dim')

    async def handle_theme_command(self, args: str, chat: ChatLog) -> None:
        """`/theme --list` lists; anything else goes to the theme flow.

        Listing is separate from opening the picker because the errors of a broken
        theme file belong somewhere readable, and the picker only shows what loaded.
        """
        if args.strip().lower() in {'--list', 'list'}:
            errors = self.reload_themes()
            await chat.add_notice(self.describe_themes(), style='white')
            for error in errors:
                await chat.add_notice(f'✖ {error}', style='yellow')
            return
        # Re-read first: a theme dropped in the folder while the app was open is
        # exactly the case this command exists for.
        self.reload_themes()
        await self.handle_theme(args, chat)

    def action_quit(self) -> None:
        """Closes the application, persisting the session."""
        self.exit()

    async def new_session(self, chat: ChatLog, args: str = '') -> None:
        """Starts an empty session, in the current workspace or in a new worktree."""
        try:
            request = parse_new_args(args)
        except UsageError as exc:
            await chat.add_notice(str(exc), style='yellow')
            return

        if request.worktree is not None:
            # The previous session is saved *before* the switch: it belongs to the
            # workspace that is about to stop being active.
            self.persist()
            try:
                switch = self.create_worktree_workspace(request.worktree)
            except WorkspaceError as exc:
                await chat.add_notice(f'✖ {exc}', style='bold red')
                return
        else:
            self.persist()
            switch = None

        self.bind_session(self._store.create_session())
        if self._scope is not None:
            self._scope.reset()
        if self._loaded_skills is not None:
            self._loaded_skills.reset()
        self.refresh_sidebar()
        await chat.clear()
        if switch is not None:
            await self.announce_switch(switch, chat)
        await chat.add_notice(f'New session {self._session.short_id}.', style='green')

    async def load_session(self, chat: ChatLog) -> None:
        """Loads a session chosen by the user, switching workspace when needed."""
        entries = catalog(self._store.cwd, base_dir=self._store.base_dir)
        if not entries:
            await chat.add_notice('No saved sessions.', style='yellow')
            return

        chosen = await self.push_screen_wait(SessionPicker(entries))
        if not chosen:
            return

        entry = next((candidate for candidate in entries if candidate.session.id == chosen), None)
        if entry is None:
            return

        self.persist()
        if not entry.is_current:
            # A session belongs to the workspace it was created in: loading it means
            # going back there, not opening it against another tree.
            try:
                switch = self.switch_workspace(str(entry.workspace.root))
            except WorkspaceError as exc:
                await chat.add_notice(f'✖ {exc}', style='bold red')
                return
            loaded = self._store.load(entry.session.id)
        else:
            switch = None
            loaded = self._store.load(entry.session.id)

        if loaded is None:
            await chat.add_notice(f'Could not read session {entry.session.short_id}.', style='red')
            return

        self.bind_session(loaded)
        if self._scope is not None:
            self._scope.reset()
        if self._loaded_skills is not None:
            self._loaded_skills.reset()
        self._restore_session_model(loaded)
        self.refresh_sidebar()
        await chat.clear()
        if switch is not None:
            await self.announce_switch(switch, chat)
        await chat.render_history(loaded.messages)
        chat.finalize()
        await chat.add_notice(f'Loaded session {loaded.short_id}.', style='green')

    def bind_session(self, session: object) -> None:
        """Points the app at ``session``: history, roadmap and persistence."""
        self._session = session
        self._history = session.messages
        self.bind_roadmap()

    def _restore_session_model(self, session) -> None:
        """Re-applies the model the conversation was using.

        A session keeps its selection on purpose: resuming a conversation on a
        different model would silently change cost and behaviour. When that
        account no longer exists, the turn keeps the current model and the chat
        says so instead of pretending nothing happened.
        """
        service = getattr(self, '_model_service', None)
        if service is None or session.selection is None:
            return
        try:
            service.apply(session.selection)
        except Exception as exc:  # noqa: BLE001 - a stale account must not break /load
            self.post_message(ChatNotice(f'Kept the current model: {exc}', style='yellow'))
            return
        self.load_model_window()
        self.refresh_sidebar()

    def bind_roadmap(self) -> None:
        """The roadmap follows the active session: each session has its own."""
        if self._roadmap_store is not None:
            self._roadmap_store.bind_session(self._session.id)

    def persist(self) -> None:
        """Saves the current session if it has messages."""
        self._sync_session_selection()
        if self._session.messages:
            self._store.save(self._session)

    def _sync_session_selection(self) -> None:
        """Copies the active model into the session before it is written."""
        service = getattr(self, '_model_service', None)
        if service is None:
            return
        self._session.selection = service.snapshot().selection

    async def handle_sidebar(self, args: str, chat: ChatLog) -> None:
        """`/sidebar` toggles; `/sidebar on|off` sets the state."""
        from src.ui.widgets.sidebar_panel import SidebarPanel

        value = args.strip().lower()
        panel = self.query_one(SidebarPanel)
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
        """`/debug` toggles the view; `/debug on|off` sets the state; `--text` dumps the report.

        The view replaces the chat, so `--text` is the only way to read
        the report inside the chat itself (and to copy it from there).
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
        """Context breakdown: measured, projected, budget and turns."""
        view = self._context_capability.last_view if self._context_capability is not None else None
        report = self._meter.report(self._history, view=view)
        lines = [render_report(report)]

        if self._workspace_manager is not None:
            workspace = self._workspace_manager.current()
            lines.append('')
            lines.append('Workspace')
            lines.append(f'  name             : {workspace.label}')
            lines.append(f'  root             : {workspace.root}')
            lines.append(f'  branch           : {workspace.branch or "(none)"}')
            if workspace.worktree is not None:
                lines.append(f'  worktree         : {"main" if workspace.worktree.is_main else "linked"}')

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
        """State of the configured MCP servers."""
        if self._mcp_manager is None or not self._mcp_manager:
            await chat.add_notice(
                'No MCP servers configured. Create .sigil/config.json with an `mcp_servers` section.',
                style='yellow',
            )
            return

        await chat.add_notice('Querying MCP servers…', style='cyan')
        statuses = await self._mcp_manager.describe(connect=True)
        await chat.add_notice(format_mcp_status(statuses), style='white')
        # `/mcp` is the diagnostic: the panel updates with what it just saw.
        self.apply_mcp_status(statuses)
