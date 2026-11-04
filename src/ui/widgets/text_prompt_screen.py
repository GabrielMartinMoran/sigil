"""A single-line text prompt.

Used where a modal with options does not fit: renaming an account, or any future
"type one value" step. Returns the stripped value, or ``None`` when cancelled.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Static

from src.ui.widgets.provider_modal import ProviderModal


class TextPromptScreen(ProviderModal):
    """Returns the typed text, or ``None``."""

    TITLE_TEXT = 'Value'
    HINT_TEXT = 'Type a value · Enter confirm · Esc cancel'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False),
    ]

    def __init__(self, *, title: str, placeholder: str = '', value: str = '') -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._value = value

    def compose_body(self) -> ComposeResult:
        yield Static('', id='prompt-status')
        yield Input(value=self._value, placeholder=self._placeholder, id='prompt-text')

    def on_mount(self) -> None:
        self.set_title(self._title)
        field = self.query_one('#prompt-text', Input)
        field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            self.query_one('#prompt-status', Static).update('[bold red]Cannot be empty.[/bold red]')
            return
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)
