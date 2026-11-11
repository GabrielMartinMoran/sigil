"""Slash commands: session, workspace, sidebar, MCP, context, theme and help."""

from __future__ import annotations

import inspect

from src.engine.context.meter.render import render_report
from src.engine.mcp.tool_models.server_status import ServerStatus
from src.engine.providers.domain.provider_error import ProviderError
from src.engine.sessions.catalog import catalog
from src.engine.shell import running as running_commands
from src.engine.workspace.arguments import UsageError, parse_new_args
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.ui.help_text import HELP_TEXT, KEYS_TEXT
from src.ui.messages import ChatNotice
from src.ui.session_refresh import bind_refreshed_session, render_refreshed_session, session_view
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.mcp_screen import MCPScreen
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.session_picker import SessionPicker

BUSY_COMMANDS = frozenset(
    {'/new', '/load', '/agent', '/provider', '/model', '/effort', '/external-files', '/mcp', '/quit'}
)
"""Commands a running turn refuses.

They are the ones that would change what the turn is working with, or end the
process: `/new` and `/load` swap the session out from under it, `/agent` swaps
the instructions and the tool set of a run whose definitions were already
computed, `/provider` adds and deletes accounts, `/model` and `/effort` would
swap the model mid-run, and `/quit` closes the app.

`/model` and `/effort` are here even though the service also refuses them: the
service refuses the *change*, and only after the picker fetched the catalogue and
opened a modal. Refusing up front is what makes the refusal nothing more than a
line in the chat.

The rest read state and are safe mid-turn: `/context`, `/debug`, `/sidebar`,
and `/help` do not touch the conversation or the model. `/mcp` changes the tool set.
"""

BUSY_REFUSAL = '{name} is not available while the agent works. Esc twice stops the turn first.'
"""What a refused command says. It names the way out, so the refusal is not a dead end."""


class CommandsMixin:
    """Dispatches the slash commands and manages session switching.

    It expects the app to define ``_store``, ``_session``, ``_history``,
    ``_scope``, ``_mcp_manager``, ``_meter``,
    ``_context_capability``, ``_workspace``, ``_roadmap_store``, ``_turn_active``,
    ``_workspace_manager`` and ``start_turn``.
    """

    _turn_active: bool

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Enter on the composer: slash command, a new turn, or steering the current one.

        While a turn runs the prompt is not blocked any more. What is typed there
        goes to that turn when it is a message, and is refused when it is a
        command that would pull the session out from under the running agent.

        The composer clears itself before posting, so a slow turn cannot leave the
        typed text sitting in the box for a second send.
        """
        text = event.value.strip()
        if not text:
            return

        self.menu_hide()
        chat = self.query_one(ChatLog)
        attachments = event.attachments

        if text.startswith('/') and not attachments:
            name, _, args = text.partition(' ')
            if self.is_refused_while_busy(name):
                await self.refuse_while_busy(text, chat)
                return
            self.run_worker(self.handle_command(name, args))
            return

        # The prompt lands at the end of the transcript, so its position there is how
        # many messages the session has *now*. Knowing it up front is what makes the
        # line clickable before the turn has finished.
        seq = len(self._session.messages) if self._session is not None else None

        if self._turn_active:
            # Queued for the run in flight: it lands before the agent decides
            # what to do next, not as a second turn competing with it.
            widget = await chat.add_user(text, queued=True, attachments=attachments, seq=seq)
            self.steer(text, widget, attachments=attachments)
            return

        await self.refresh_active_session(chat)
        seq = len(self._history)
        await chat.add_user(text, attachments=attachments, seq=seq)
        self.start_turn(text, attachments=attachments)

    async def refresh_active_session(self, chat: ChatLog) -> None:
        view = session_view(self)
        latest = view._store.refresh(view._session)
        if latest is None:
            return
        await render_refreshed_session(view, latest, chat)
        await chat.add_notice('Session updated by another process; reloaded the latest version.', style='yellow')

    def is_refused_while_busy(self, name: str) -> bool:
        """Whether ``name`` is one of the commands a running turn forbids.

        ``_turn_active`` and not ``_current_run``: the flag is set the moment the
        message is accepted, so a command typed in the gap before the worker
        starts is refused too.
        """
        if name == '/mcp' and getattr(self, '_summoner', None) is not None and self._summoner.running():
            return True
        return self._turn_active and name in BUSY_COMMANDS

    async def refuse_while_busy(self, command: str, chat: ChatLog) -> None:
        """The one refusal: a notice in the chat, the command back in the prompt.

        It lives next to ``BUSY_COMMANDS`` because there is more than one way
        into the commands: `on_input_submitted` (typed) and ``menu_choose`` (the
        `Enter` that picks the highlighted row of the menu). A guard on only one
        of them is not a guard; it is a guard with a door next to it.

        Nothing else happens: the session, the history, the run and the chat are
        left exactly as they were.
        """
        await chat.add_notice(BUSY_REFUSAL.format(name=command.split(' ', 1)[0]), style='yellow')
        # Back in the prompt, so the command is one Enter away once the turn
        # ends instead of lost. Both paths clear the field before calling.
        prompt = self.query_one(PromptInput)
        prompt.value = command
        prompt.cursor_position = len(command)

    async def handle_command(self, name: str, args: str = '') -> None:
        """Runs the requested command.

        The busy check is here too, at the choke point every path goes through,
        and not only in the callers: it is the last chance to stop a command that
        would swap the session out from under a running turn.
        """
        chat = self.query_one(ChatLog)
        if self.is_refused_while_busy(name):
            await self.refuse_while_busy(f'{name} {args}'.strip(), chat)
            return

        handlers = {
            '/new': lambda: self.new_session(chat, args),
            '/load': lambda: self.load_session(chat),
            '/agent': lambda: self.show_agent(chat, args),
            '/provider': lambda: self.show_providers(chat),
            '/model': lambda: self.show_models(chat, args),
            '/effort': lambda: self.show_effort(chat, args),
            '/external-files': lambda: self.handle_external_files(args, chat),
            '/reasoning-visibility': lambda: self.handle_reasoning_visibility(args, chat),
            '/tools': lambda: self.handle_tools(args, chat),
            '/alerts': lambda: self.handle_alerts(args, chat),
            '/theme': lambda: self.handle_theme_command(args, chat),
            '/sidebar': lambda: self.handle_sidebar(args, chat),
            '/mcp': lambda: self.show_mcp(chat),
            '/context': lambda: self.show_context(chat),
            '/rollback': lambda: self.show_rollback(chat, args),
            '/debug': lambda: self.handle_debug(args, chat),
            '/help': lambda: self.show_help(chat),
            '/quit': lambda: self.action_quit(),
        }
        handler = handlers.get(name)
        if handler is None:
            await chat.add_notice(f'Unknown command: {name}', style='red')
            return
        result = handler()
        # Not every handler is a coroutine: `/quit` closes the app and returns
        # `None`. Awaiting the value of a plain lambda raised a `TypeError` that
        # came out as a worker failure, which is a crash for the user.
        if inspect.isawaitable(result):
            await result

    async def handle_external_files(self, args: str, chat: ChatLog) -> None:
        """Show or change this session's file access boundary."""
        value = args.strip().lower()
        if value not in {'', 'allow', 'deny'}:
            await chat.add_notice('Usage: /external-files [allow|deny]', style='yellow')
            return
        await self.refresh_active_session(chat)
        if value:
            self._session.allow_external_files = value == 'allow'
            self._store.save(self._session)
        state = 'allowed' if self._session.allow_external_files else 'denied'
        await chat.add_notice(f'Outside-workspace file access is {state} for this session.', style='yellow')

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
        """Closes the application, persisting the session.

        With a turn running there is a run in flight and, possibly, shell
        commands: closing the terminal only ends the UI, so the commands are
        killed and the run cancelled first, and what was completed is already on
        disk thanks to the checkpoints.

        The two-press `Esc` guard is skipped on purpose: `Ctrl+Q` is the exit, not
        a mistake, and making the exit conditional would leave a user who lost
        the key stuck in a turn.
        """
        running_commands.kill_all()
        # `abort_turn` also stops the asynchronous subagents, before the save below:
        # they outlive a turn on purpose, but not the process, and a tree on disk
        # with a node saying it is still working would be a lie.
        self.abort_turn()
        self.persist()
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

        session = self._store.draft_session()
        service = getattr(self, '_model_service', None)
        if service is not None:
            session.selection = service.snapshot().selection
        # A new session inherits the agent in effect: starting a conversation
        # should not silently drop back to the default profile.
        profile = self.active_agent()
        session.agent = profile.name if profile is not None else ''
        self.bind_session(session)
        model_error = None
        select_model = getattr(self, '_on_agent_selected', None)
        if select_model is not None and profile is not None:
            try:
                snapshot = select_model(profile.name)
                if snapshot is not None:
                    self._after_model_change(snapshot)
            except ProviderError as exc:
                model_error = str(exc)
        if self._scope is not None:
            self._scope.reset()
        self.refresh_sidebar()
        await chat.clear()
        if model_error is not None:
            await chat.add_notice(f'Agent model: {model_error}', style='bold red')
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
        self._restore_session_model(loaded)
        self.refresh_sidebar()
        await chat.clear()
        if switch is not None:
            await self.announce_switch(switch, chat)
        await chat.render_history(loaded.messages, subagents=self.tree_nodes())
        chat.finalize()
        await chat.add_notice(f'Loaded session {loaded.short_id}.', style='green')

    def bind_session(self, session: object) -> None:
        """Points the app at ``session``: history, agent, tree, roadmap and persistence."""
        self._session = session
        mcp = getattr(self, '_mcp_manager', None)
        if mcp is not None and hasattr(mcp, 'set_overrides'):
            mcp.set_overrides(session.mcp_overrides)
        callback = getattr(self, '_on_bind_session', None)
        if callback is not None:
            callback(session)
        self._history = session.messages
        # The measured half of the context lives with the request that produced it, so
        # it travels on the session and is read back here. `/load` used to leave the
        # budget at zero, and `/context` then promised room the next request did not
        # have.
        self._meter.restore(session)
        # What the last request of the *previous* conversation did is not this one's
        # answer: the view is a report about a request, and this session has not made
        # one yet.
        self.reset_context_view()
        self.bind_agent(getattr(session, 'agent', ''))
        # The tree travels with the conversation: loading a session brings back the
        # subagents it summoned, and the view of a node of the old one has to close.
        self.remember_tree(session)
        self.bind_roadmap()
        # Another conversation is another window title: the repository is the same
        # and the title is not.
        self.paint_window_title()

    def reset_context_view(self) -> None:
        """Drops the last projected view, if the app has the capability."""
        capability = getattr(self, '_context_capability', None)
        reset = getattr(capability, 'reset', None)
        if callable(reset):
            reset()

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
        """The roadmap follows the active session: each session has its own.

        Re-pointing the store is not enough: the panel holds the store it painted
        last, and a registry that swaps its stores underneath leaves that reference
        aimed at a roadmap nobody owns anymore. That is what made a new session show
        the plan of the one before it — the store was emptied, the panel was still
        reading the old object.
        """
        if self._roadmap_store is not None:
            self._roadmap_store.bind_session(self._session.id)
        self._point_roadmap_at_view()

    def persist(self) -> None:
        """Saves the current session if it has messages."""
        self._sync_session_selection()
        if self._session.messages:
            # What the meter measured rides with the row: `/load` reads it back.
            self._meter.stamp(self._session)
            current = self._store.save_current(self._session)
            if current is not self._session:
                view = session_view(self)
                bind_refreshed_session(view, current)
                view.post_message(
                    ChatNotice('Session updated by another process; reloaded the latest version.', style='yellow')
                )

    def _sync_session_selection(self) -> None:
        """Copies the active model into the session before it is written."""
        service = getattr(self, '_model_service', None)
        if service is None:
            return
        self._session.selection = service.snapshot().selection

    async def handle_sidebar(self, args: str, chat: ChatLog) -> None:
        """`/sidebar` toggles; `/sidebar on|off` sets the state.

        It is one panel and one flag: the diagnostics and the roadmap are read as
        a single answer —"what is it doing, with what, and how far has it got?"— so
        there is no state where one is visible and the other is not.
        """
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

        statuses = await self._mcp_manager.describe()
        await self.push_screen_wait(
            MCPScreen(statuses, self._toggle_session_mcp, lambda: self._mcp_manager.describe(connect=True))
        )
        self.refresh_mcp_status()

    async def _toggle_session_mcp(self, name: str, enabled: bool) -> ServerStatus:
        self._session.mcp_overrides[name] = enabled
        self._mcp_manager.set_overrides(self._session.mcp_overrides)
        if not self._session._draft or self._session.messages:
            current = self._store.save_current(self._session)
            if current is not self._session:
                bind_refreshed_session(session_view(self), current)
        if not enabled:
            await self._mcp_manager.stop_disabled(name)
        return self._mcp_manager.get(name).status()
