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
from src.ui.messages import ChatNotice
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

    # --- the command -------------------------------------------------------

    async def show_agent(self, chat: ChatLog, argument: str = '') -> None:
        """`/agent` opens the picker; `/agent <name>` switches directly."""
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

    # --- applying the choice -----------------------------------------------

    def apply_agent(self, name: str, chat: ChatLog) -> None:
        """Activates ``name``, records it on the session and repaints the bar."""
        try:
            profile = self._agent_registry.activate(name)
        except AgentNotFound as exc:
            chat.post_message(ChatNotice(str(exc), style='bold red'))
            return
        self.remember_agent(profile)
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
        try:
            bar = self.query_one(StatusBar)
        except NoMatches:
            return
        bar.set_agent(profile.label if profile is not None else '')

    def active_agent(self) -> AgentProfile | None:
        registry = getattr(self, '_agent_registry', None)
        return registry.active if registry is not None else None

    def agent_notices(self) -> tuple[str, ...]:
        """Profile files that could not be parsed, for the startup report."""
        registry = getattr(self, '_agent_registry', None)
        return tuple(registry.iter_errors()) if registry is not None else ()


__all__ = ['AgentChoice', 'AgentMixin']
