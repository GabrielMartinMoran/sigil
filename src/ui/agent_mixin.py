"""`/agent`, the picker behind it, and the agent name in the status bar.

The registry is the source of truth: this mixin only opens the screen, applies
the choice to the session and repaints the chrome. Switching agent does not
rebuild the agent object — the instruction block and the tool permissions read
the registry on every request, so the next turn already runs under the new
profile.

The switch is refused while a turn runs. Unlike a model swap, which only changes
what comes next, an agent swap changes the instructions **and** which tools exist
in the middle of a run whose tool set was already computed.
"""

from __future__ import annotations

from typing import Any

from textual.css.query import NoMatches

from src.engine.agents.profile.models.agent_not_found import AgentNotFound
from src.engine.agents.profile.models.agent_profile import AgentProfile
from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.providers.domain.provider_error import ProviderError
from src.ui.messages import ChatNotice
from src.ui.service_client import ServiceClient, ServiceClientError
from src.ui.turn_mixin import failure_notice
from src.ui.widgets.agent_picker_screen import AgentChoice, AgentPickerScreen
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.status_bar import StatusBar

NO_PROFILES_NOTICE = (
    'No agent profiles found. Add one at .sigil/agents/<name>.md '
    'in the project, or ~/.sigil/agents/<name>.md for every project. '
    'A profile with `role: subagent` is summoned by an agent, not selected here.'
)
"""What `/agent` says when there is nothing to switch to, and where to put one."""


class AgentMixin:
    """`/agent [name]`, the `ctrl+a` picker and the active agent in the chrome.

    It expects the app to define ``_agent_registry``, ``_session`` and
    ``is_refused_while_busy``.
    """

    _agent_registry: Any
    _session: Any
    _client: ServiceClient | None

    # --- the command -------------------------------------------------------

    async def show_agent(self, chat: ChatLog, argument: str = '') -> None:
        """`/agent` opens the picker; `/agent <name>` switches directly.

        An attached terminal delegates the switch: which profile the session runs
        under is the service's fact, and this process must not stamp it locally.
        The picker stays here, and lists the profiles the service reports.
        """
        if self._client is not None:
            await self._service_agent(self._client, chat, argument)
            return
        registry = getattr(self, '_agent_registry', None)
        if registry is None:
            await chat.add_notice(NO_PROFILES_NOTICE, style='yellow')
            return
        # Rescanning lets a profile written during the session show up without a
        # restart; the active one survives the rescan.
        registry.refresh()
        for error in self.agent_notices():
            await chat.add_notice(f'✖ {error}', style='yellow')
        if not registry.agents():
            await chat.add_notice(NO_PROFILES_NOTICE, style='yellow')
            return

        if argument.strip():
            self.apply_agent(argument.strip().lower(), chat)
            return
        await self.open_agent()

    async def open_agent(self) -> None:
        """The picker, from any context (`/agent` and `ctrl+a`)."""
        if self._client is not None:
            await self._service_agent(self._client, self.query_one(ChatLog), '')
            return
        registry = getattr(self, '_agent_registry', None)
        if registry is None or not registry.agents():
            return
        if self.is_refused_while_busy('/agent'):
            await self.refuse_while_busy('/agent', self.query_one(ChatLog))
            return

        # `agents()` and not `list()`: the picker offers what the user may *be*, and
        # a subagent is only ever started by another agent.
        choice = await self.push_screen_wait(AgentPickerScreen(registry.agents(), current=registry.active_name))
        if choice is None:
            return
        self.apply_agent(choice.name, self.query_one(ChatLog))

    async def _service_agent(self, client: ServiceClient, chat: ChatLog, argument: str) -> None:
        """Switches the profile through the service and paints what it answered.

        Nothing local is stamped: `session.agent` and the active name stay the
        service's, so a second terminal that refreshes sees the same agent and
        this one does not drift into a second truth.
        """
        try:
            state = await client.state()
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        selectable = [
            str(row.get('name') or '')
            for row in state.get('agents') or []
            if str(row.get('role') or AgentRole.AGENT) == str(AgentRole.AGENT)
        ]
        active = str((state.get('session') or {}).get('agent') or '')
        name = argument.strip().lower()
        if not name:
            offered = self._offered_agents(selectable)
            if not offered:
                await chat.add_notice(NO_PROFILES_NOTICE, style='yellow')
                return
            choice = await self.push_screen_wait(AgentPickerScreen(offered, current=active))
            if choice is None:
                return
            name = choice.name
        try:
            payload = await client.select_agent(name)
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        await self.refresh_service_state()
        selected = str((payload.get('agent') or {}).get('name') or name)
        await chat.add_notice(f'Agent: {selected.replace("-", " ").title()}', style='green')

    def _offered_agents(self, names: list[str]) -> list[AgentProfile]:
        """The service's selectable names, as rows this picker can paint.

        The permission line comes from the profile file: it is on disk in the
        workspace the service runs in, and the projection carries no permissions,
        so reading it from the file is the only way to show the truth instead of
        a guessed default. A name the service knows and this process cannot read
        is left out of the list rather than rendered with invented permissions;
        `/agent <name>` still reaches the service with it.
        """
        registry = getattr(self, '_agent_registry', None)
        if registry is None:
            return []
        registry.refresh()
        wanted = set(names)
        return [profile for profile in registry.agents() if profile.name in wanted]

    # --- applying the choice -----------------------------------------------

    def apply_agent(self, name: str, chat: ChatLog) -> None:
        """Activates ``name``, records it on the session and repaints the bar."""
        previous = self._agent_registry.active_name
        try:
            profile = self._agent_registry.activate(name)
            select_model = getattr(self, '_on_agent_selected', None)
            snapshot = select_model(profile.name) if select_model is not None else None
        except (AgentNotFound, ProviderError) as exc:
            if previous and self._agent_registry.active_name != previous:
                self._agent_registry.activate(previous)
            chat.post_message(ChatNotice(str(exc), style='bold red'))
            return
        self.remember_agent(profile)
        if snapshot is not None:
            self._after_model_change(snapshot)
        chat.post_message(ChatNotice(f'Agent: {profile.label} — {profile.permissions.describe()}', style='green'))

    def bind_agent(self, name: str) -> None:
        """Applies the agent a session was using.

        A profile removed from disk since the session was saved is not an error:
        the registry already fell back to a default, and losing a conversation
        over a deleted `.md` would be worse than a different agent reading it.
        """
        registry = getattr(self, '_agent_registry', None)
        if registry is None:
            return
        if name:
            try:
                registry.activate(name)
            except AgentNotFound:
                pass
        self.remember_agent(registry.active)

    def remember_agent(self, profile: AgentProfile | None) -> None:
        """Stamps the agent on the session and repaints the status bar.

        Every session carries its agent, the way it carries its model: a new
        session inherits the one in effect, and a saved conversation comes back
        with the one it was built under.

        The root of the tree follows the same name. A tree that kept saying
        ``Wizard`` over a session run by another profile would be lying about the
        branch everything else hangs from, and the root's name is the only thing in
        it that is not a record of something that happened.
        """
        if self._session is not None:
            self._session.agent = profile.name if profile is not None else ''
        rename = getattr(self, 'rename_tree_root', None)
        if callable(rename) and profile is not None:
            rename(profile.name)
        self.refresh_agent()

    def refresh_agent(self) -> None:
        """Repaints the status bar with the profile in effect.

        The bar may not be composed yet (the agent can be switched from code
        before mount), and its absence is not worth failing anything over.
        """
        profile = self.active_agent()
        label = profile.label if profile is not None else ''
        service = getattr(self, '_service_state', None)
        if self._client is not None and isinstance(service, dict):
            # The projection is the authority while attached: the local registry
            # is a catalogue of files, not a statement about this session.
            name = str((service.get('session') or {}).get('agent') or '')
            label = name.replace('-', ' ').title() if name else ''
        try:
            bar = self.query_one(StatusBar)
        except NoMatches:
            return
        bar.set_agent(label)

    def active_agent(self) -> AgentProfile | None:
        registry = getattr(self, '_agent_registry', None)
        return registry.active if registry is not None else None

    def agent_notices(self) -> tuple[str, ...]:
        """Profile files that could not be parsed, for the startup report."""
        registry = getattr(self, '_agent_registry', None)
        return tuple(registry.iter_errors()) if registry is not None else ()


__all__ = ['AgentChoice', 'AgentMixin']
