"""Pantalla modal para elegir una sesión guardada."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.sessions.session_store import Session


class SessionPicker(ModalScreen[str | None]):
    """Devuelve el id de la sesión elegida (o ``None`` si se cancela)."""

    BINDINGS: ClassVar[list[Binding]] = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, sessions: list[Session]) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        options = [
            Option(f'[bold]{session.short_id}[/bold]  [dim]{session.preview[:60]}[/dim]', id=session.id)
            for session in self._sessions
        ]
        yield Vertical(
            Static('Select a session (↑/↓, Enter; Esc to cancel)'),
            OptionList(*options, id='sessions'),
            id='picker',
        )

    def on_mount(self) -> None:
        if self._sessions:
            self.query_one(OptionList).highlighted = 0
            self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
