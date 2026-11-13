"""Choose a model across accounts, with persistent account-specific favourites."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.ui.widgets.provider_modal import ProviderModal


@dataclass(frozen=True, slots=True)
class AccountCatalog:
    account_id: str
    alias: str
    catalog: ModelCatalog
    provider: str = ''
    """What the account talks to — ``"OpenRouter"``, ``"OpenCode Go"``.

    The alias is a name the user chose and it is the only handle the service takes;
    the provider is what answers. A list that shows one without the other asks the
    reader to remember which is which.
    """

    @property
    def display_name(self) -> str:
        return f'{self.alias} · {self.provider}' if self.provider else self.alias


@dataclass(frozen=True, slots=True)
class GroupedModelChoice:
    account_id: str
    alias: str
    model: ModelCapability
    provider: str = ''

    @property
    def display_name(self) -> str:
        return f'{self.alias} · {self.provider}' if self.provider else self.alias


class GroupedModelScreen(ProviderModal):
    TITLE_TEXT = 'Models'
    HINT_TEXT = 'Filter · ↑/↓ move · Ctrl+F favourite · Enter select · Esc cancel'
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', show=False, priority=True),
        Binding('up', 'move(-1)', show=False, priority=True),
        Binding('down', 'move(1)', show=False, priority=True),
        Binding('pageup', 'move(-5)', show=False, priority=True),
        Binding('pagedown', 'move(5)', show=False, priority=True),
        Binding('tab', 'complete', show=False, priority=True),
        Binding('ctrl+f', 'favorite', show=False, priority=True),
        Binding('enter', 'choose', show=False, priority=True),
    ]

    def __init__(
        self,
        catalogs: Sequence[AccountCatalog],
        favorites: tuple[tuple[str, str], ...],
        *,
        on_favorites: Callable[[tuple[tuple[str, str], ...]], None],
    ) -> None:
        super().__init__()
        self._catalogs = tuple(catalogs)
        self._favorites = list(favorites)
        self._on_favorites = on_favorites
        self.visible_models: list[GroupedModelChoice] = []

    def compose_body(self) -> ComposeResult:
        yield Input(placeholder='Filter models or providers…', id='grouped-filter')
        yield Static('', id='grouped-status')
        yield OptionList(id='picker-models')

    def on_mount(self) -> None:
        self._refresh('')
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def _refresh(self, query: str, selected: tuple[str, str] | None = None) -> None:
        needle = query.casefold().strip()
        self.visible_models = [
            GroupedModelChoice(group.account_id, group.alias, model, group.provider)
            for group in self._catalogs
            for model in group.catalog.models
            if needle in f'{group.alias} {group.provider} {model.name} {model.display_name}'.casefold()
        ]
        self.visible_models.sort(
            key=lambda row: (
                (row.account_id, row.model.name) not in self._favorites,
                row.alias.casefold(),
                row.model.label.casefold(),
            )
        )
        custom_entry = bool(needle and not self.visible_models)
        if custom_entry:
            self.visible_models = [
                GroupedModelChoice(group.account_id, group.alias, ModelCapability(query.strip()), group.provider)
                for group in self._catalogs
            ]
        options = self.query_one(OptionList)
        options.clear_options()
        last_group = None
        for index, row in enumerate(self.visible_models):
            favorite = (row.account_id, row.model.name) in self._favorites
            group = ('Favorites', '') if favorite else ('Provider', row.alias)
            text = Text()
            if group != last_group:
                # The heading names the account when the group is one; under
                # `Favorites` the rows have to carry it themselves.
                text.append(('Favorites' if favorite else row.display_name) + '\n', style='bold cyan')
                last_group = group
            text.append('★ ' if favorite else '  ', style='yellow')
            text.append(f'Use {row.model.label} as typed' if custom_entry else row.model.label, style='bold')
            if favorite:
                text.append(f'  {row.display_name}', style='dim')
            text.append(f'\n    {row.model.window_label} ctx · {row.model.efforts_label}', style='dim')
            options.add_option(Option(text, id=str(index)))
        failures = sum(bool(group.catalog.error) for group in self._catalogs)
        self.query_one('#grouped-status', Static).update(
            f'{len(self.visible_models)} models'
            + (f' · {failures} provider lists unavailable or incomplete' if failures else '')
        )
        options.highlighted = (
            next(
                (
                    index
                    for index, row in enumerate(self.visible_models)
                    if (row.account_id, row.model.name) == selected
                ),
                0,
            )
            if self.visible_models
            else None
        )

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        if options.option_count:
            options.highlighted = min(max((options.highlighted or 0) + delta, 0), options.option_count - 1)

    def _selected(self) -> GroupedModelChoice | None:
        index = self.query_one(OptionList).highlighted
        return self.visible_models[index] if index is not None and index < len(self.visible_models) else None

    def action_complete(self) -> None:
        row = self._selected()
        if row is not None:
            field = self.query_one(Input)
            field.value = row.model.name
            field.cursor_position = len(field.value)

    def action_favorite(self) -> None:
        row = self._selected()
        if row is None:
            return
        key = (row.account_id, row.model.name)
        if key in self._favorites:
            self._favorites.remove(key)
        else:
            self._favorites.append(key)
        self._on_favorites(tuple(self._favorites))
        self._refresh(self.query_one(Input).value, key)

    def action_choose(self) -> None:
        row = self._selected()
        if row is not None:
            self.dismiss(row)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        del event
        self.action_choose()

    def action_cancel(self) -> None:
        self.dismiss(None)
