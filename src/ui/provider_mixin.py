"""Provider commands and screen orchestration.

The mixin is the seam between the UI and ``ModelService``: it opens screens,
runs the calls that need a worker (network, OAuth), and reports every outcome in
the chat. Screens only collect input; the service only enforces rules. That
split is what keeps the destructive and the network paths in one testable place.
"""

from __future__ import annotations

import asyncio
from typing import Any

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

        accounts = service.list_accounts()
        await chat.add_notice('Fetching the model lists…', style='cyan')
        results = await asyncio.gather(
            *(service.catalog_for(account.alias) for account in accounts), return_exceptions=True
        )
        catalogs = []
        for account, result in zip(accounts, results, strict=True):
            if isinstance(result, BaseException):
                await chat.add_notice(f'{account.alias}: model list unavailable ({result})', style='yellow')
                result = ModelCatalog(error=str(result))
            catalogs.append(AccountCatalog(account.id, account.alias, result))
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
        await chat.add_notice(f'Model: {snapshot.label}', style='green')

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
        chat.post_message(ChatNotice(f'Model: {snapshot.label}', style='green'))

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
            f'Model   : {snapshot.label}',
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
        await chat.add_notice(f'Active: {snapshot.label}', style='green')

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


__all__ = ['ProviderMixin']
