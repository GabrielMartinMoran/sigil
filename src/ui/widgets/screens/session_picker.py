"""Modal screen to pick a saved session, grouped by workspace."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

from src.core.sessions.catalog_entry import CatalogEntry
from src.ui.widgets.models.session_option import session_option


class SessionPicker(ModalScreen[str | None]):
    """Returns the id of the chosen session (or ``None`` if cancelled)."""

    BINDINGS: ClassVar[list[Binding]] = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, entries: list[CatalogEntry]) -> None:
        super().__init__()
        self._entries = entries

    def compose(self) -> ComposeResult:
        options = [session_option(entry) for entry in self._entries]
        yield Vertical(
            Static('Select a session (↑/↓, Enter; Esc to cancel) · ● current workspace'),
            OptionList(*options, id='sessions'),
            id='picker',
        )

    def on_mount(self) -> None:
        if self._entries:
            highlighted = next((index for index, entry in enumerate(self._entries) if entry.is_current), 0)
            options = self.query_one(OptionList)
            options.highlighted = highlighted
            options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
