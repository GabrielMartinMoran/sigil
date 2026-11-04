"""Model picker: filter the account's catalogue and pick one.

The list arrives already fetched, so typing never triggers a request. When the
catalogue is short because it could not be refreshed, the reason is shown at the
top instead of leaving the user wondering why a model is missing.

A free-text entry is always allowed: OpenCode Go has no catalogue endpoint, and a
model released this morning would be unavailable for hours. The picker accepts
what the user types rather than pretending the list is complete.

The filter keeps the focus while typing, so the arrows and Tab are intercepted
here and forwarded to the list: without that, Up/Down never reach the options and
the list looks frozen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.ui.widgets.provider_modal import ProviderModal

FREE_TEXT_ID = '__free_text__'

MOVES: dict[str, int] = {'up': -1, 'down': 1, 'pageup': -5, 'pagedown': 5}


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """The model the user picked."""

    model: str
    from_catalog: bool = True


class ModelPickerScreen(ProviderModal):
    """Returns the chosen model name, or ``None``."""

    TITLE_TEXT = 'Model'
    HINT_TEXT = 'Type to filter · ↑/↓ move · Enter select · Tab complete · Esc cancel'

    # Priority bindings on the screen itself: the focused widget is the Input, so
    # without `priority` the arrows are swallowed before the list can see them.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False, priority=True),
        Binding('up', 'move(-1)', 'Up', show=False, priority=True),
        Binding('down', 'move(1)', 'Down', show=False, priority=True),
        Binding('pageup', 'move(-5)', 'Page up', show=False, priority=True),
        Binding('pagedown', 'move(5)', 'Page down', show=False, priority=True),
        Binding('tab', 'complete', 'Complete', show=False, priority=True),
        Binding('enter', 'choose', 'Choose', show=False, priority=True),
    ]

    def __init__(
        self,
        catalog: ModelCatalog,
        *,
        account_label: str = '',
        current: str = '',
    ) -> None:
        super().__init__()
        self._catalog = catalog
        self._account_label = account_label
        self._current = current
        self._visible: list[ModelCapability] = []

    def compose_body(self) -> ComposeResult:
        yield Static('', id='picker-status')
        yield Input(placeholder='Filter by name…', id='picker-filter')
        yield OptionList(id='picker-models')

    def on_mount(self) -> None:
        self.set_title(f'Model — {self._account_label}' if self._account_label else 'Model')
        self._refresh_list('')
        self.query_one('#picker-filter', Input).focus()

    # --- filtering ---------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != 'picker-filter':
            return
        self._refresh_list(event.value)

    def _refresh_list(self, query: str) -> None:
        needle = query.strip().lower()
        self._visible = [model for model in self._catalog.models if _matches(model, needle)]
        options = self.query_one(OptionList)
        options.clear_options()

        for model in self._visible:
            options.add_option(Option(_row(model, current=self._current), id=model.name))

        if needle and not any(model.name == query.strip() for model in self._visible):
            options.add_option(
                Option(f'[italic]Use “{query.strip()}” as typed[/italic]', id=FREE_TEXT_ID),
            )

        self._update_status(len(self._visible))
        if self._visible or needle:
            options.highlighted = 0

    def _update_status(self, shown: int) -> None:
        status = self.query_one('#picker-status', Static)
        if self._catalog.error:
            status.update(f'[yellow]{self._catalog.error}[/yellow]')
            return
        total = len(self._catalog.models)
        source = 'from the provider' if self._catalog.source == 'remote' else 'built-in list'
        status.update(f'[dim]{shown} of {total} models, {source}[/dim]')

    # --- actions -----------------------------------------------------------

    def action_move(self, delta: int) -> None:
        """Moves the highlight by ``delta`` rows, clamped to the list."""
        options = self.query_one(OptionList)
        count = options.option_count
        if not count:
            return
        current = options.highlighted or 0
        options.highlighted = min(max(current + delta, 0), count - 1)

    def action_complete(self) -> None:
        """Tab: copies the highlighted model into the filter instead of choosing it.

        It is the "complete the word" half of Tab; Enter is what commits.
        """
        option = self.query_one(OptionList).highlighted_option
        if option is None or option.id is None or option.id == FREE_TEXT_ID:
            return
        field = self.query_one('#picker-filter', Input)
        field.value = str(option.id)
        field.cursor_position = len(field.value)

    def action_choose(self) -> None:
        """Enter: commits the highlighted row, or the typed text as a last resort."""
        options = self.query_one(OptionList)
        option = options.highlighted_option
        if option is None or option.id is None:
            typed = self.query_one('#picker-filter', Input).value.strip()
            if typed:
                self.dismiss(ModelChoice(model=typed, from_catalog=False))
            return
        self._choose(str(option.id))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # The screen-level `enter` binding already handles this; swallowing the
        # event here keeps the Input from doing its own submit.
        if event.input.id == 'picker-filter':
            self.action_choose()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(str(event.option.id))

    def _choose(self, option_id: str) -> None:
        if option_id == FREE_TEXT_ID:
            typed = self.query_one('#picker-filter', Input).value.strip()
            self.dismiss(ModelChoice(model=typed, from_catalog=False))
            return
        self.dismiss(ModelChoice(model=option_id))

    def action_cancel(self) -> None:
        self.dismiss(None)


def _matches(model: ModelCapability, needle: str) -> bool:
    if not needle:
        return True
    return needle in model.name.lower() or needle in model.display_name.lower()


def _row(model: ModelCapability, *, current: str) -> str:
    marker = '[green]●[/green] ' if model.name == current else '  '
    window = f'[dim]{model.window_label:>4} ctx[/dim]'
    efforts = f'[dim]{model.efforts_label}[/dim]'
    label = model.display_name if model.display_name != model.name else model.name
    name = f'[bold]{label}[/bold]'
    if model.display_name != model.name:
        name = f'{name} [dim]({model.name})[/dim]'
    return f'{marker}{name}\n    {window} · {efforts}'


def render_models(catalog: ModelCatalog) -> Sequence[str]:
    """Plain rows, for `/model --list` and for tests."""
    return [f'{model.name} ({model.window_label}, {model.efforts_label})' for model in catalog.models]
