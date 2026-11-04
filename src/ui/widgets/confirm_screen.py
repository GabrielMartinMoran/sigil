"""A yes/no confirmation with the safe answer selected.

Only used for a destructive action (removing an account). The default highlight
is "no", so a stray `Enter` cannot delete anything.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.ui.widgets.provider_modal import ProviderModal

YES = 'yes'
NO = 'no'


class ConfirmScreen(ProviderModal):
    """Returns ``True`` only when the user explicitly confirms."""

    TITLE_TEXT = 'Confirm'
    HINT_TEXT = '↑/↓ choose · Enter confirm · Esc cancel'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False),
    ]

    def __init__(self, question: str, *, confirm_label: str = 'Yes, delete it') -> None:
        super().__init__()
        self._question = question
        self._confirm_label = confirm_label

    def compose_body(self) -> ComposeResult:
        yield Static(self._question, id='confirm-question')
        yield OptionList(
            Option(f'[bold red]{self._confirm_label}[/bold red]', id=YES),
            Option('No, keep it', id=NO),
            id='confirm-options',
        )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        # The safe answer is highlighted on purpose.
        options.highlighted = 1
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id) == YES)

    def action_cancel(self) -> None:
        self.dismiss(False)
