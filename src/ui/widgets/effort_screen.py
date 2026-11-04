"""Reasoning-effort picker.

`/effort high` works, but typing a level is a poor way to pick one of five. This
screen lists exactly the levels the active model accepts, plus the two values that
are not levels: ``default`` (send nothing, keep the model's own behaviour) and
``off``.

Offering only the accepted levels is the point: a list that includes levels the
model rejects turns a menu into a guessing game with an error message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.providers.domain.thinking import EFFORT_LEVELS, ThinkingChoice
from src.ui.widgets.provider_modal import ProviderModal

DEFAULT_VALUE = 'default'
OFF_VALUE = 'off'


@dataclass(frozen=True, slots=True)
class EffortChoice:
    """What the user picked."""

    effort: ThinkingChoice


class EffortScreen(ProviderModal):
    """Returns the chosen reasoning level, or ``None``."""

    TITLE_TEXT = 'Reasoning effort'
    HINT_TEXT = '↑/↓ move · Enter apply · Esc cancel'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False, priority=True),
        Binding('up', 'move(-1)', 'Up', show=False, priority=True),
        Binding('down', 'move(1)', 'Down', show=False, priority=True),
        Binding('enter', 'choose', 'Apply', show=False, priority=True),
    ]

    def __init__(
        self,
        current: ThinkingChoice = 'default',
        accepted: tuple[str, ...] = (),
        *,
        model: str = '',
    ) -> None:
        """
        Args:
            current: The level in effect, highlighted on open.
            accepted: Levels the model reports. Empty means "no effort control",
                and only the two non-levels are offered.
            model: Model id, for the title.
        """
        super().__init__()
        self._current = current
        self._accepted = tuple(level for level in EFFORT_LEVELS if level in accepted)
        self._model = model

    def compose_body(self) -> ComposeResult:
        yield Static('', id='effort-status')
        yield OptionList(*self._options(), id='effort-options')

    def on_mount(self) -> None:
        self.set_title(f'Reasoning effort — {self._model}' if self._model else 'Reasoning effort')
        self._update_status()
        options = self.query_one(OptionList)
        options.highlighted = self._current_index()
        options.focus()

    def _options(self) -> list[Option]:
        rows = [
            Option(_row(DEFAULT_VALUE, 'let the model decide', current=self._current), id=DEFAULT_VALUE),
        ]
        for level in self._accepted:
            rows.append(Option(_row(level, _describe(level), current=self._current), id=level))
        rows.append(
            Option(_row(OFF_VALUE, 'ask for the least reasoning it allows', current=self._current), id=OFF_VALUE)
        )
        return rows

    def _values(self) -> list[str]:
        return [DEFAULT_VALUE, *self._accepted, OFF_VALUE]

    def _current_index(self) -> int:
        values = self._values()
        return values.index(self._current) if self._current in values else 0

    def _update_status(self) -> None:
        status = self.query_one('#effort-status', Static)
        if not self._accepted:
            status.update('[dim]This model reports no reasoning levels; only default and off apply.[/dim]')
            return
        status.update(f'[dim]{self._model or "This model"} accepts: {", ".join(self._accepted)}[/dim]')

    # --- actions -----------------------------------------------------------

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        count = options.option_count
        if not count:
            return
        current = options.highlighted or 0
        options.highlighted = min(max(current + delta, 0), count - 1)

    def action_choose(self) -> None:
        option = self.query_one(OptionList).highlighted_option
        if option is None or option.id is None:
            return
        self.dismiss(EffortChoice(effort=str(option.id)))  # type: ignore[arg-type]

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(EffortChoice(effort=str(event.option.id)))  # type: ignore[arg-type]

    def action_cancel(self) -> None:
        self.dismiss(None)


def _row(value: str, description: str, *, current: str) -> str:
    marker = '[green]●[/green] ' if value == current else '  '
    return f'{marker}[bold]{value}[/bold]  [dim]{description}[/dim]'


def _describe(level: str) -> str:
    return {
        'minimal': 'barely think',
        'low': 'think a little',
        'medium': 'the usual balance',
        'high': 'think harder',
        'xhigh': 'think much harder',
        'max': 'spend everything on reasoning',
    }.get(level, '')
