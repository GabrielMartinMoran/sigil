"""Provider commands and screen orchestration.

The mixin is the seam between the UI and ``ModelService``: it opens screens,
runs the calls that need a worker (network, OAuth), and reports every outcome in
the chat. Screens only collect input; the service only enforces rules. That
split is what keeps the destructive and the network paths in one testable place.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from src.engine.agents.model_config import AgentModelConfig
from src.engine.agents.model_defaults import ConfigScope, ModelDefaultsState
from src.engine.agents.profile.models.agent_profile import AgentProfile
from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.model_capability import ModelCatalog
from src.engine.providers.domain.provider_error import (
    AliasError,
    ProviderError,
)
from src.engine.providers.domain.thinking import EFFORT_LEVELS
from src.engine.providers.snapshot import ModelSnapshot
from src.ui.messages import ChatNotice
from src.ui.model_view_mixin import effort_label
from src.ui.widgets.account_form_screen import AccountFormScreen, NewAccount
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.confirm_screen import ConfirmScreen
from src.ui.widgets.effort_screen import EffortScreen
from src.ui.widgets.grouped_model_screen import AccountCatalog, GroupedModelScreen
from src.ui.widgets.model_defaults_screen import DefaultsChoice, DefaultsRow, ModelDefaultsScreen
from src.ui.widgets.model_picker_screen import ModelChoice
from src.ui.widgets.provider_screen import ProviderAction, ProviderScreen
from src.ui.widgets.sidebar_panel import ModelState


class ProviderMixin:
    """`/provider`, `/model` and `/effort`, plus the screens behind them.

    It expects the app to define ``_model_service``, ``_login_flow``,
    ``_session`` and ``_history``.
    """

    _model_service: Any
    _login_flow: Any
    _session: Any
    _meter: Any
    _history: list[Any]

    # --- commands ----------------------------------------------------------

    async def show_providers(self, chat: ChatLog) -> None:
        """Opens the account list and performs the chosen action, in a loop.

        The loop is what makes the flow feel like a screen rather than a chain of
        modal boxes: after adding an account you land back in the list.
        """
        while True:
            action = await self.push_screen_wait(
                ProviderScreen(self._model_service.list_accounts(), self._active_account_id())
            )
            if action is None:
                return
            keep_going = await self._run_action(action, chat)
            if not keep_going:
                return

    async def _run_action(self, action: ProviderAction, chat: ChatLog) -> bool:
        if action.action == 'add':
            return await self._add_account(chat)
        if action.action == 'activate':
            await self._activate(action.alias, chat)
            return True
        if action.action == 'rename':
            await self._rename(action.alias, chat)
            return True
        if action.action == 'delete':
            await self._delete(action.alias, chat)
            return True
        return True

    async def show_models(self, chat: ChatLog, query: str = '') -> None:
        """Chooses a model and reasoning effort across all configured accounts."""
        service = self._model_service
        if service is None or not service.list_accounts():
            await chat.add_notice('Configure a provider first: /provider', style='yellow')
            return

        await chat.add_notice('Fetching the model lists…', style='cyan')
        catalogs = await self._account_catalogs(chat)
        if not catalogs:
            return
        choice = await self.push_screen_wait(
            GroupedModelScreen(catalogs, self._preferences.model_favorites, on_favorites=self._save_model_favorites)
        )
        if choice is None:
            return
        effort = await self.push_screen_wait(EffortScreen(accepted=choice.model.efforts, model=choice.model.name))
        if effort is None:
            return
        try:
            snapshot = service.select(choice.alias, choice.model.name, effort.effort)
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return
        self._after_model_change(snapshot)
        await chat.add_notice(f'Model: {snapshot.display_name}', style='green')

    async def _account_catalogs(self, chat: ChatLog) -> list[AccountCatalog]:
        """Every account's catalogue, fetched at once, with the failures kept.

        A provider that is down shortens the list instead of closing the screen: the
        picker says which lists are incomplete and still offers the rest.
        """
        accounts = self._model_service.list_accounts()
        results = await asyncio.gather(
            *(self._model_service.catalog_for(account.alias) for account in accounts), return_exceptions=True
        )
        catalogs = []
        for account, result in zip(accounts, results, strict=True):
            if isinstance(result, BaseException):
                await chat.add_notice(f'{account.alias}: model list unavailable ({result})', style='yellow')
                result = ModelCatalog(error=str(result))
            catalogs.append(AccountCatalog(account.id, account.alias, result, account.provider_label))
        return catalogs

    # --- the defaults ------------------------------------------------------

    async def show_default_models(self, chat: ChatLog) -> None:
        """`/default-models`: the model each agent starts on, and the one for the rest.

        The screen collects a target and a destination, the same pickers `/model` uses
        collect the account, the model and the effort, and the write goes to the file
        the destination names. Nothing here changes the model in effect: a default is
        what a *new* conversation starts on, and moving the one in front of the user
        from a screen about defaults is the surprise `/model` exists to avoid.
        """
        service = getattr(self, '_model_service', None)
        read = getattr(self, '_model_defaults_state', None)
        save = getattr(self, '_save_model_default', None)
        if service is None or read is None or save is None:
            await chat.add_notice('Model defaults are not editable from this client.', style='yellow')
            return
        accounts = service.list_accounts()
        if not accounts:
            await chat.add_notice('Configure a provider first: /provider', style='yellow')
            return

        await chat.add_notice('Fetching the model lists…', style='cyan')
        catalogs = await self._account_catalogs(chat)
        if not catalogs:
            return
        providers = {account.alias: account.provider_label for account in accounts}
        while True:
            state = read()
            rows = defaults_rows(
                state,
                profiles=self._profiles(),
                active=self._active_agent_name(),
                providers=providers,
            )
            choice = await self.push_screen_wait(ModelDefaultsScreen(rows, state))
            if choice is None:
                return
            if choice.clear:
                await self._clear_default(choice, save, read, chat)
            else:
                await self._set_default(choice, catalogs, save, read, chat)
            # The list comes back either way: a cancelled picker is not a reason to
            # close the flow, and Esc on the list is one press away.

    def _profiles(self) -> dict[str, AgentProfile]:
        """Every installed profile, by name: agents and subagents alike.

        `list()` and not `agents()`: the picker hides subagents because the user
        cannot *be* one, but `agents.<name>` decides what a subagent runs on just the
        same, and its default is set here like any other.
        """
        registry = getattr(self, '_agent_registry', None)
        if registry is None:
            return {}
        return {profile.name: profile for profile in registry.list()}

    def _active_agent_name(self) -> str:
        registry = getattr(self, '_agent_registry', None)
        return getattr(registry, 'active_name', '') or ''

    async def _set_default(
        self,
        choice: DefaultsChoice,
        catalogs: list[AccountCatalog],
        save: Callable[[str | None, AgentModelConfig | None, ConfigScope], tuple[str, ...]],
        read: Callable[[], ModelDefaultsState],
        chat: ChatLog,
    ) -> None:
        """Collects the model and the effort, then writes the entry."""
        picked = await self.push_screen_wait(
            GroupedModelScreen(catalogs, self._preferences.model_favorites, on_favorites=self._save_model_favorites)
        )
        if picked is None:
            return
        effort = await self.push_screen_wait(EffortScreen(accepted=picked.model.efforts, model=picked.model.name))
        if effort is None:
            return

        config = AgentModelConfig(account=picked.alias, model=picked.model.name, effort=effort.effort)
        errors = save(choice.row.target, config, choice.scope)
        if errors:
            for error in errors:
                await chat.add_notice(f'✖ {error}', style='bold red')
            return

        after = read()
        await chat.add_notice(
            f'{choice.row.label}: {picked.display_name} · {config.model} · {config.effort} '
            f'→ {_file_label(after, choice.scope)}',
            style='green',
        )
        if choice.scope not in after.sources(choice.row.target):
            # The write returned no error and the file still does not show it: the
            # list is about to lie unless this says so.
            await chat.add_notice('The entry did not land; the config errors are in the list.', style='yellow')
        elif after.effective(choice.row.target) != config:
            await chat.add_notice(
                f'{_higher_file(after, choice.row.target)} still overrides this one; clear it with d on the same row.',
                style='yellow',
            )
        await chat.add_notice(_effect_note(choice.row), style='dim')

    async def _clear_default(
        self,
        choice: DefaultsChoice,
        save: Callable[[str | None, AgentModelConfig | None, ConfigScope], tuple[str, ...]],
        read: Callable[[], ModelDefaultsState],
        chat: ChatLog,
    ) -> None:
        errors = save(choice.row.target, None, choice.scope)
        if errors:
            for error in errors:
                await chat.add_notice(f'✖ {error}', style='bold red')
            return
        after = read()
        await chat.add_notice(f'Cleared {choice.row.label} from the {_file_label(after, choice.scope)}', style='green')

    def _save_model_favorites(self, favorites: tuple[tuple[str, str], ...]) -> None:
        self._preferences = self._preferences.with_favorites(favorites)
        self.save_preferences()

    async def open_models(self) -> None:
        """`alt+m` / `/model` with no argument: the picker, from any context.

        It goes through the same mid-turn refusal as the command: the shortcut
        is another door into the same room, and swapping the model under a
        running turn is exactly what the referee is for.
        """
        if self.is_refused_while_busy('/model'):
            await self.refuse_while_busy('/model', self.query_one(ChatLog))
            return
        await self.show_models(self.query_one(ChatLog))

    async def open_effort(self) -> None:
        """`ctrl+l` / `/effort` with no argument: the effort picker."""
        if self.is_refused_while_busy('/effort'):
            await self.refuse_while_busy('/effort', self.query_one(ChatLog))
            return
        await self.show_effort(self.query_one(ChatLog))

    def _select_model(self, choice: ModelChoice, chat: ChatLog, query: str) -> None:
        del query
        try:
            snapshot = self._model_service.select(
                self._model_service.snapshot().alias,
                choice.model,
            )
        except ProviderError as exc:
            chat.post_message(ChatNotice(str(exc), style='bold red'))
            return
        self._after_model_change(snapshot)
        chat.post_message(ChatNotice(f'Model: {snapshot.display_name}', style='green'))

    async def show_effort(self, chat: ChatLog, argument: str = '') -> None:
        """Sets the reasoning level, or opens the picker when there is no argument."""
        service = self._model_service
        if service is None or not service.is_configured():
            await chat.add_notice('Configure a provider first: /provider', style='yellow')
            return

        if not argument.strip():
            await self._pick_effort(chat)
            return

        try:
            updated = service.set_thinking(argument.strip().lower())
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return
        self._after_model_change(updated)
        await chat.add_notice(f'Reasoning effort: {updated.thinking}', style='green')

    async def _pick_effort(self, chat: ChatLog) -> None:
        """The effort screen, offering only the levels the model accepts."""
        snapshot = self._model_service.snapshot()
        choice = await self.push_screen_wait(
            EffortScreen(
                current=snapshot.thinking,
                accepted=snapshot.efforts,
                model=snapshot.model,
            )
        )
        if choice is None:
            return
        try:
            updated = self._model_service.set_thinking(choice.effort)
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return
        self._after_model_change(updated)
        await chat.add_notice(f'Reasoning effort: {updated.thinking}', style='green')

    def _effort_help(self, snapshot: ModelSnapshot) -> str:
        accepted = snapshot.efforts or ()
        available = ', '.join(accepted) if accepted else 'none reported'
        lines = [
            f'Model   : {snapshot.display_name}',
            f'Effort  : {snapshot.thinking}',
            f'Accepted: {available}',
            '',
            f'Set it with /effort <level>. Levels: {", ".join(EFFORT_LEVELS)}, off, default.',
        ]
        if not accepted:
            lines.append('This model does not report reasoning levels; only default and off apply.')
        return '\n'.join(lines)

    # --- account actions ---------------------------------------------------

    async def _add_account(self, chat: ChatLog) -> bool:
        values = await self.push_screen_wait(AccountFormScreen())
        if values is None:
            return True

        if values.auth is AuthMethod.OAUTH:
            await self._add_oauth_account(values, chat)
            return True

        await chat.add_notice(f'Checking the key for {values.kind.label}…', style='cyan')
        try:
            snapshot = await self._model_service.add_api_key(
                values.kind,
                values.alias,
                values.api_key,
                variant=values.variant,
            )
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return True

        await self._report_added(snapshot, chat)
        return True

    async def _add_oauth_account(self, values: NewAccount, chat: ChatLog) -> None:
        await chat.add_notice(
            'Opening the browser. If nothing happens, the sign-in URL is printed here.',
            style='cyan',
        )
        flow = self._login_flow
        if flow is None:
            await chat.add_notice('Browser sign-in is not available in this build.', style='bold red')
            return
        flow.on_url = lambda url: self.post_message(ChatNotice(f'Sign in at: {url}', style='cyan'))
        try:
            snapshot = await self._model_service.add_oauth(values.kind, values.alias, flow)
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return
        await self._report_added(snapshot, chat)

    async def _report_added(self, snapshot: ModelSnapshot, chat: ChatLog) -> None:
        await chat.add_notice(
            f'Added {snapshot.alias} · {snapshot.account.provider_label} → {snapshot.model}',
            style='green',
        )
        account_id = snapshot.account.id if snapshot.account else ''
        probe = self._model_service.last_probe(account_id)
        if probe is not None and not probe.is_valid:
            await chat.add_notice(
                f'Saved without a successful check: {probe.detail or probe.outcome}. '
                'Use /model to pick a model if the list came back short.',
                style='yellow',
            )
        self._after_model_change(snapshot)

    async def _activate(self, alias: str, chat: ChatLog) -> None:
        try:
            account = self._model_service.find(alias)
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return

        model = account.last_model
        if not model:
            catalog = await self._model_service.catalog_for(alias)
            model = catalog.models[0].name if catalog.models else ''
        if not model:
            await chat.add_notice(f'{alias} has no model yet: pick one with /model', style='yellow')
            return

        try:
            snapshot = self._model_service.select(alias, model)
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return
        self._after_model_change(snapshot)
        await chat.add_notice(f'Active: {snapshot.display_name}', style='green')

    async def _rename(self, alias: str, chat: ChatLog) -> None:
        from src.ui.widgets.text_prompt_screen import TextPromptScreen

        new_alias = await self.push_screen_wait(
            TextPromptScreen(title=f'Rename {alias}', placeholder='New alias…', value=alias)
        )
        if not new_alias or new_alias == alias:
            return
        try:
            renamed = self._model_service.rename(alias, new_alias)
        except AliasError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return
        self.refresh_sidebar()
        await chat.add_notice(f'Renamed to {renamed.alias}.', style='green')

    async def _delete(self, alias: str, chat: ChatLog) -> None:
        confirmed = await self.push_screen_wait(
            ConfirmScreen(f"Remove '{alias}' and its stored credential?", confirm_label='Yes, remove it')
        )
        if not confirmed:
            return
        try:
            self._model_service.remove(alias)
        except ProviderError as exc:
            await chat.add_notice(str(exc), style='bold red')
            return
        self._after_model_change(self._model_service.snapshot())
        await chat.add_notice(f'Removed {alias}.', style='green')

    # --- shared plumbing ---------------------------------------------------

    def _active_account_id(self) -> str | None:
        service = getattr(self, '_model_service', None)
        if service is None:
            return None
        snapshot = service.snapshot()
        return snapshot.account.id if snapshot.account is not None else None

    def _after_model_change(self, snapshot: ModelSnapshot) -> None:
        """Propagates a new model to the session, the meter and the chrome."""
        if self._session is not None:
            self._session.selection = snapshot.selection
            if self._session.messages:
                self.persist()
        self.load_model_window()
        self.refresh_sidebar()

    def model_state(self) -> ModelState | None:
        """What the sidebar shows. ``None`` when nothing is configured."""
        service = getattr(self, '_model_service', None)
        if service is None:
            return None
        snapshot = service.snapshot()
        if not snapshot.is_configured or snapshot.account is None:
            return None
        return ModelState(
            alias=snapshot.alias,
            provider=snapshot.account.provider_label,
            auth=snapshot.account.auth_label,
            model=snapshot.model,
            # The effort in effect, not the selected one: mid-turn the history
            # says which level is actually going out.
            effort=effort_label(snapshot, self.effort_in_effect()),
        )


def _notice(text: str, style: str) -> ChatNotice:
    """Chat notice for callbacks that are not ``async``."""
    return ChatNotice(text, style=style)


def defaults_rows(
    state: ModelDefaultsState,
    *,
    profiles: Mapping[str, AgentProfile] | None = None,
    active: str = '',
    providers: Mapping[str, str] | None = None,
) -> tuple[DefaultsRow, ...]:
    """The screen's rows: the default first, then the agents, then the subagents.

    Both roles are listed. `agents.<name>` decides what a profile runs on whether the
    user picks it or a `summon` starts it, so a subagent is a target with a default of
    its own — hiding it would leave its model reachable only by editing the file.

    A profile the config names but the registry no longer has is listed last, and
    marked. An entry for a deleted agent is exactly the one worth clearing, and
    hiding it would leave `agents.<gone>` in the file with no way out from the TUI.
    """
    installed = dict(profiles or {})
    accounts = dict(providers or {})
    agents = sorted((item for item in installed.values() if item.role.selectable), key=lambda item: item.name)
    subagents = sorted((item for item in installed.values() if not item.role.selectable), key=lambda item: item.name)
    orphans = sorted(name for name in state.agent_names() if name not in installed)

    rows = [
        DefaultsRow(
            target=None,
            label='Default for new conversations',
            summary=_summary(state.effective(None), state.invalid(None), accounts),
            sources=state.sources(None),
            invalid=state.invalid(None),
        )
    ]
    rows.extend(_profile_row(profile, state, accounts, active) for profile in (*agents, *subagents))
    rows.extend(
        DefaultsRow(
            target=name,
            label=name,
            summary=_summary(state.effective(name), state.invalid(name), accounts),
            sources=state.sources(name),
            invalid=state.invalid(name),
            installed=False,
        )
        for name in orphans
    )
    return tuple(rows)


def _profile_row(
    profile: AgentProfile, state: ModelDefaultsState, accounts: Mapping[str, str], active: str
) -> DefaultsRow:
    return DefaultsRow(
        target=profile.name,
        label=profile.label,
        summary=_summary(state.effective(profile.name), state.invalid(profile.name), accounts),
        sources=state.sources(profile.name),
        invalid=state.invalid(profile.name),
        active=profile.name == active,
        role=profile.role,
    )


def _summary(config: AgentModelConfig | None, invalid: bool, accounts: Mapping[str, str]) -> str:
    """One entry as a reader recognizes it, or why there is nothing to show.

    ``accounts`` maps an alias to its provider. The config stores the alias, and an
    alias on its own says nothing about who answers the request — so the provider is
    shown too, and its absence is what tells the user the account is gone.
    """
    if invalid:
        return 'invalid entry: see the config errors below'
    if config is None:
        return 'not set'
    provider = accounts.get(config.account)
    served_by = f'{config.account} · {provider}' if provider else config.account
    summary = f'{served_by} · {config.model} · {config.effort}'
    if accounts and provider is None:
        summary += f' · account {config.account!r} is not configured'
    return summary


def _file_label(state: ModelDefaultsState, scope: ConfigScope) -> str:
    """The file a write went to, named the way the user chose it."""
    if state.fixed is not None:
        return f'SIGIL_CONFIG ({state.fixed})'
    name = 'project config' if scope is ConfigScope.PROJECT else 'global config'
    return f'{name} ({state.path_for(scope)})'


def _higher_file(state: ModelDefaultsState, target: str | None) -> str:
    """The other file that still decides for ``target``."""
    for scope in state.destinations:
        if scope in state.sources(target):
            return _file_label(state, scope)
    return 'Another config file'


def _effect_note(row: DefaultsRow) -> str:
    """Where the default just written actually takes effect.

    A subagent and an agent are reached differently, and saying "new conversations"
    about a subagent would be wrong: it never starts a conversation, it answers a
    summon.
    """
    if row.target is None:
        return 'New conversations start on it; this session keeps its model.'
    if row.role is AgentRole.SUBAGENT:
        return 'Every summon of that subagent uses it; this session keeps its model.'
    return 'New conversations and that agent use it; this session keeps its model.'


__all__ = ['ProviderMixin', 'defaults_rows']
