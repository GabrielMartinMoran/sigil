"""The account list: add, remove, rename and activate providers.

The screen collects a decision and returns it as an action value; it never calls
the service. That keeps the destructive paths (remove) and the network paths
(add) in the mixin, where they can be run as workers and reported in the chat.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.providers.domain.provider_account import ProviderAccount
from src.ui.widgets.provider_modal import ProviderModal

Action = Literal['add', 'delete', 'rename', 'activate']


@dataclass(frozen=True, slots=True)
class ProviderAction:
    """What the user chose in the list."""

    action: Action
    alias: str = ''


class ProviderScreen(ProviderModal):
    """Lists the accounts and returns the action to perform."""

    TITLE_TEXT = 'Providers'
    HINT_TEXT = 'a add · Enter activate · r rename · d delete · Esc close'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Close', show=False),
        Binding('a', 'add', 'Add', show=False),
        Binding('r', 'rename', 'Rename', show=False),
        Binding('d', 'delete', 'Delete', show=False),
    ]

    def __init__(self, accounts: Sequence[ProviderAccount], active_id: str | None = None) -> None:
        super().__init__()
        self._accounts = list(accounts)
        self._active_id = active_id

    def compose_body(self) -> ComposeResult:
        if not self._accounts:
            yield Static(
                'No providers yet.\n\nPress [bold]a[/bold] to add one: an OpenRouter, OpenCode or OpenAI account.',
                id='provider-empty',
            )
            return
        yield OptionList(*self._options(), id='provider-list')

    def _options(self) -> list[Option]:
        options: list[Option] = []
        for account in self._accounts:
            marker = '●' if account.id == self._active_id else '○'
            model = account.last_model or 'no model yet'
            label = f'{marker} [bold]{account.alias}[/bold]  [dim]{account.provider_label} · {account.auth_label}[/dim]'
            label = f'{label}\n    [dim]{model}[/dim]'
            options.append(Option(label, id=account.id))
        return options

    def on_mount(self) -> None:
        self.set_title(self._title())
        if not self._accounts:
            return
        options = self.query_one(OptionList)
        options.highlighted = 0
        options.focus()

    def _title(self) -> str:
        count = len(self._accounts)
        if count == 0:
            return 'Providers'
        return f'Providers ({count})'

    # --- selection ---------------------------------------------------------

    def _highlighted(self) -> ProviderAccount | None:
        if not self._accounts:
            return None
        options = self.query_one(OptionList)
        index = options.highlighted
        if index is None or not 0 <= index < len(self._accounts):
            return None
        return self._accounts[index]

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        del event
        account = self._highlighted()
        if account is not None:
            self.dismiss(ProviderAction('activate', account.alias))

    # --- actions -----------------------------------------------------------

    def action_add(self) -> None:
        self.dismiss(ProviderAction('add'))

    def action_rename(self) -> None:
        account = self._highlighted()
        if account is not None:
            self.dismiss(ProviderAction('rename', account.alias))

    def action_delete(self) -> None:
        account = self._highlighted()
        if account is not None:
            self.dismiss(ProviderAction('delete', account.alias))

    def action_cancel(self) -> None:
        self.dismiss(None)
