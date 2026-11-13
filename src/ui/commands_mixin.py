"""Slash commands: session, workspace, sidebar, MCP, context, theme and help."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.engine.context.meter.render import render_report
from src.engine.mcp.tool_models.server_status import ServerStatus
from src.engine.providers.domain.provider_error import ProviderError
from src.engine.sessions.catalog import CatalogEntry, catalog
from src.engine.sessions.session_store import Session, SessionSummary
from src.engine.workspace.arguments import UsageError, parse_new_args
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.engine.workspace.models.worktree import Workspace, Worktree
from src.ui.command_transport import TransportState, transport_for, unavailable_notice
from src.ui.help_text import HELP_TEXT, KEYS_TEXT
from src.ui.messages import ChatNotice
from src.ui.service_client import ServiceClient, ServiceClientError
from src.ui.session_refresh import bind_refreshed_session, render_refreshed_session, session_view
from src.ui.turn_mixin import failure_notice
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

In an attached terminal the same set is refused from the service's own `busy`
flag, read from `/api/state`. Which process notices first is not the point: a
command whose action would change the session must not open a panel it cannot
complete, and the service would refuse the change anyway.

The rest read state and are safe mid-turn: `/context`, `/debug`, `/sidebar`,
and `/help` do not touch the conversation or the model. `/mcp` changes the tool set.
"""

BUSY_REFUSAL = 'A turn is running: finish or stop it before you use {name}. Esc twice stops the turn first.'

BUSY_UNKNOWN = (
    'The service did not report whether a turn is running, so {name} was not started. '
    'Try again once its state can be read.'
)
"""What a command says when the projection omits `busy`.

Not the busy sentence: that one asserts a turn. This one says what is true — the
state could not be read — and refuses anyway, because a command that changes the
session must not proceed on a guess about whether one is running.
"""
"""What a refused command says. It names the way out, so the refusal is not a dead end."""


def _tokens(value: Any) -> str:
    """A token count the service sent, or a plain statement that it did not."""
    return f'{value:,}' if isinstance(value, int) else 'not reported by the service'


def _percent(value: Any) -> str:
    return f'{value:.0f}%' if isinstance(value, (int, float)) else 'not reported'


def render_service_context(context: Mapping[str, Any], workspace: Mapping[str, Any] | None = None) -> str:
    """The `/context` report, built from the numbers the service measured.

    Nothing here is measured locally: the session belongs to the service, and one
    conversation cannot have two meters that disagree. A datum the service did not
    send is named as absent rather than printed as a zero, because a zero is a
    claim about the session and this terminal is in no position to make it. That is
    also why a cold session reads `0 (0%)` and never `?`: the service sends the real
    zero for a session that has not made a request yet.

    The scope section of the old report is absent, not empty: no endpoint projects
    the active directories or the instruction files (see docs/architecture.md).
    """
    budget = context.get('budget') or {}
    cost = context.get('cost') or {}
    lines = ['Context (from the service)']
    lines.append(f'  window           : {_tokens(context.get("window"))}')
    used = context.get('used')
    if used is None:
        lines.append('  used             : the service reported no projection')
    else:
        lines.append(f'  used             : {used:,} ({_percent(context.get("percent"))})')
    lines.append(f'  measured         : {_tokens(context.get("measured"))}')
    lines.append(f'  projected        : {_tokens(context.get("projected"))}')

    lines.append('')
    lines.append('Cost')
    total = cost.get('total')
    if total is None:
        lines.append('  total            : not priced yet')
    else:
        suffix = ' (at list price)' if cost.get('approximate') else ''
        lines.append(f'  total            : {total}{suffix}')
    lines.append(f'  requests         : {_tokens(cost.get("requests"))}')

    lines.append('')
    lines.append('Budget')
    lines.append(f'  tools            : {_tokens(budget.get("tools"))}')
    lines.append(f'  instructions     : {_tokens(budget.get("instructions"))}')
    lines.append(f'  output reserve   : {_tokens(budget.get("output_reserve"))}')
    lines.append(f'  safety reserve   : {_tokens(budget.get("safety_reserve"))}')
    lines.append(f'  available        : {_tokens(budget.get("available_for_history"))}')

    if workspace:
        worktree = workspace.get('worktree') or {}
        lines.append('')
        lines.append('Workspace')
        lines.append(f'  name             : {workspace.get("label") or "(unnamed)"}')
        lines.append(f'  root             : {workspace.get("root") or "(not reported)"}')
        lines.append(f'  branch           : {workspace.get("branch") or "(none)"}')
        if worktree:
            lines.append(f'  worktree         : {"main" if worktree.get("is_main") else "linked"}')
    return '\n'.join(lines)


def _catalog_entry(row: Mapping[str, Any], *, current: str) -> CatalogEntry:
    """One projected session, rebuilt as the row the picker paints.

    Every field comes from `/api/sessions`: the id, the title, the preview, the
    date, the workspace label and whether it is a worktree. Nothing is looked up
    locally, so the list a terminal shows is the list the service has, and the
    same session appears in the same place in both clients. The workspace object
    is a display carrier built from those facts — its label and `is_worktree` are
    the projection's, not a guess from the directory name.
    """
    root = Path(str(row.get('workspace') or '.'))
    worktree = Worktree(path=root, is_main=not bool(row.get('is_worktree')))
    session = SessionSummary(
        id=str(row.get('id') or ''),
        workspace=str(row.get('workspace') or ''),
        title=row.get('title'),
        preview=str(row.get('preview') or ''),
        updated_at=str(row.get('updated_at') or ''),
        profile=str(row.get('profile') or ''),
    )
    return CatalogEntry(
        session=session,
        workspace=Workspace(root=root, worktree=worktree),
        is_current=session.id == current,
    )


def _server_status(row: Mapping[str, Any]) -> ServerStatus:
    """One MCP server, as the modal's own model. The wire is not the widget."""
    return ServerStatus(
        name=str(row.get('name') or ''),
        transport=str(row.get('transport') or ''),
        enabled=bool(row.get('enabled')),
        prefix=str(row.get('prefix') or ''),
        tools=[str(tool) for tool in row.get('tools') or ()],
        error=None if row.get('error') is None else str(row['error']),
        source=str(row.get('source') or ''),
    )


class CommandsMixin:
    """Dispatches the slash commands and manages session switching.

    It expects the app to define ``_store``, ``_session``, ``_history``,
    ``_scope``, ``_mcp_manager``, ``_meter``,
    ``_context_capability``, ``_workspace``, ``_roadmap_store``, ``_turn_active``,
    ``_workspace_manager`` and ``start_turn``.
    """

    _turn_active: bool
    _client: ServiceClient | None

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

    async def _guard_delegated_busy(self, name: str, chat: ChatLog) -> bool:
        """Refreshes the service's busy flag once before a delegated command acts.

        The cached state can be a minute old, and a picker opened on a stale `busy` is
        a panel the user cannot complete. One read narrows the window. When the read
        fails, or the projection does not say whether a turn runs, the command stops
        here: this process cannot see the session's state, so it must not change it.
        Both cases are refusals rather than guesses, and neither is silent.
        """
        if self._client is None or name not in BUSY_COMMANDS:
            return False
        try:
            state = await self._client.state()
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return True
        self._apply_service_state(state)
        if 'busy' not in state:
            await chat.add_notice(BUSY_UNKNOWN.format(name=name), style='yellow')
            return True
        if state.get('busy'):
            await self.refuse_while_busy(name, chat)
            return True
        return False

    def is_refused_while_busy(self, name: str) -> bool:
        """Whether ``name`` is one of the commands a running turn forbids.

        ``_turn_active`` and not ``_current_run``: the flag is set the moment the
        message is accepted, so a command typed in the gap before the worker
        starts is refused too.

        An attached terminal has no turn of its own, so the flag it reads is the
        service's: ``/api/state`` carries ``busy``, and that is the same fact the
        service would use to refuse the change. Reading it here is what keeps the
        refusal free of a panel — the picker would otherwise open, fetch a
        catalogue and only then be told the session is in the way.
        """
        if name not in BUSY_COMMANDS:
            return False
        if name == '/mcp' and getattr(self, '_summoner', None) is not None and self._summoner.running():
            return True
        if self._client is not None:
            state = getattr(self, '_service_state', None)
            return bool(isinstance(state, dict) and state.get('busy'))
        return self._turn_active

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

        So is the transport decision. An attached terminal cannot run a command
        whose effect belongs to the service — the handler would reach for a store
        and an engine this process does not own, and fail somewhere the user
        cannot read. The transport table says, once, which commands run here and
        which only have a sentence to say; this is the only place that reads it.
        """
        chat = self.query_one(ChatLog)
        if await self._guard_delegated_busy(name, chat):
            return
        if self.is_refused_while_busy(name):
            await self.refuse_while_busy(f'{name} {args}'.strip(), chat)
            return

        transport = transport_for(name)
        if (
            self._client is not None
            and transport is not None
            and transport.state in {TransportState.NOT_WIRED, TransportState.BLOCKED}
        ):
            # A row that cannot act here answers once and does nothing else: no
            # panel, no queue row, no local write. A delegated row is not refused
            # here — its handler is the sender, and it runs below. Only the two
            # rows with no way out are answered at the door.
            await chat.add_notice(unavailable_notice(transport), style='yellow')
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
        if self._client is not None:
            await self._service_external_files(self._client, value, chat)
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
        self._cancel_shell()
        # `abort_turn` also stops the asynchronous subagents, before the save below:
        # they outlive a turn on purpose, but not the process, and a tree on disk
        # with a node saying it is still working would be a lie.
        self.abort_turn()
        self.persist()
        self.exit()

    async def new_session(self, chat: ChatLog, args: str = '') -> None:
        """Starts an empty session, in the service when this terminal is attached."""
        try:
            request = parse_new_args(args)
        except UsageError as exc:
            await chat.add_notice(str(exc), style='yellow')
            return

        if self._client is not None:
            await self._service_new_session(self._client, request, chat)
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

    async def _service_new_session(self, client: ServiceClient, request: Any, chat: ChatLog) -> None:
        """Creates the session in the service and repaints from what it returned.

        `--worktree` creates the worktree in the service, then starts the session
        there: the engine cuts the tree and moves, so the session is created where
        the service now runs instead of in a directory it has left.
        """
        worktree = (request.worktree or '').strip() if request.worktree is not None else ''
        label = ''
        if worktree:
            try:
                switch = await client.create_worktree(worktree)
            except ServiceClientError as exc:
                await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
                return
            label = str((switch.get('workspace') or {}).get('label') or '')
        try:
            session = await client.new_session()
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        await chat.clear()
        await self.refresh_service_state()
        await self.load_service_history(rebuild=True)
        # The cursor restarts from the state just read: the new session has its own
        # sequence, and keeping the old number would skip whatever it publishes.
        self._service_cursor = self._state_cursor()
        prefix = f'Workspace: {label}. ' if label else ''
        await chat.add_notice(f'{prefix}New session {session.get("short_id") or ""}.', style='green')

    async def _service_load_session(self, client: ServiceClient, chat: ChatLog) -> None:
        """Lists, opens and repaints from the service, never from a local catalog."""
        try:
            listing = await client.sessions()
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        current = str(listing.get('current') or '')
        entries = [_catalog_entry(row, current=current) for row in listing.get('sessions') or []]
        if not entries:
            await chat.add_notice('No saved sessions.', style='yellow')
            return
        chosen = await self.push_screen_wait(SessionPicker(entries))
        if not chosen:
            return
        try:
            session = await client.open_session(chosen)
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        await chat.clear()
        await self.refresh_service_state()
        await self.load_service_history(rebuild=True)
        self._service_cursor = self._state_cursor()
        # Opening a session can move the service to its workspace; the header is
        # repainted from the same state read above rather than from a local guess.
        repaint_badge = getattr(self, 'refresh_workspace_badge', None)
        if callable(repaint_badge):
            repaint_badge()
        await chat.add_notice(f'Loaded session {session.get("short_id") or ""}.', style='green')

    async def load_session(self, chat: ChatLog) -> None:
        """Loads a session chosen by the user, switching workspace when needed."""
        if self._client is not None:
            await self._service_load_session(self._client, chat)
            return
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

    def bind_session(self, session: Session) -> None:
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
        """Context breakdown: the service's measurement when attached.

        An attached terminal paints the service's numbers, never this process's
        meter: the session it is looking at is the service's, and a local meter
        would report a second, disagreeing truth about it. The scope section is
        absent because no endpoint projects it (see docs/architecture.md).
        """
        if self._client is not None:
            await self._service_context(self._client, chat)
            return
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
        """State of the configured MCP servers, from the service when attached.

        The modal is still this terminal's: only the list and the toggle travel. A
        terminal that read its own manager would report the servers *it* loaded,
        which in an attached terminal is none of them.
        """
        if self._client is not None:
            await self._service_mcp(self._client, chat)
            return
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

    async def _service_context(self, client: ServiceClient, chat: ChatLog) -> None:
        """The meter, the budget and the bill, as the service measured them.

        Two reads and no arithmetic: `/api/context` answers for the session the
        service owns, and `/api/state` carries the workspace it is running in. A
        terminal that measured its own history would describe a conversation it
        never had, which is exactly the false zero this replaces.
        """
        try:
            context = await client.context()
            state = await client.state()
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        workspace = state.get('workspace')
        await chat.add_notice(
            render_service_context(context, workspace if isinstance(workspace, dict) else None), style='white'
        )

    async def _service_external_files(self, client: ServiceClient, value: str, chat: ChatLog) -> None:
        """Shows, or changes, the service's file boundary for this session."""
        try:
            if value:
                session = await client.external_files(value == 'allow')
            else:
                session = (await client.state()).get('session') or {}
        except ServiceClientError as exc:
            # The refusal is the server's, with the server's reason, and the
            # message it refused did not change it: nothing is painted as done.
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        allowed = session.get('external_files')
        if not isinstance(allowed, bool):
            await chat.add_notice("The service did not report this session's file-access setting.", style='yellow')
            return
        state = 'allowed' if allowed else 'denied'
        await chat.add_notice(f'Outside-workspace file access is {state} for this session.', style='yellow')

    async def _service_mcp(self, client: ServiceClient, chat: ChatLog) -> None:
        """Lists the service's MCP servers; the modal stays this terminal's."""
        try:
            rows = await client.mcp_servers()
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        statuses = [_server_status(row) for row in rows]
        if not statuses:
            await chat.add_notice(
                'No MCP servers configured. Create .sigil/config.json with an `mcp_servers` section.',
                style='yellow',
            )
            return
        current = {status.name: status for status in statuses}

        async def toggle(name: str, enabled: bool) -> ServerStatus:
            """Toggles through the service, keeping the row honest on a refusal."""
            try:
                updated = await client.toggle_mcp(name, enabled)
            except ServiceClientError as exc:
                # A refusal changed nothing, so the row keeps the state the service
                # still has. Annotated here, and not by repainting a change that
                # did not happen.
                await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
                return current[name]
            for row in updated:
                status = _server_status(row)
                current[status.name] = status
            return current[name]

        await self.push_screen_wait(MCPScreen(statuses, toggle))
        await self.refresh_service_state()

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
