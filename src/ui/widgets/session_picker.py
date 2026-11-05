"""Modal screen to pick a saved session, grouped by workspace."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.sessions.catalog import CatalogEntry


def session_option(entry: CatalogEntry) -> Option:
    """One line per session: workspace badge, short id and preview.

    The badge is what tells two sessions with the same preview apart, which is
    exactly what happens when the same task is tried in the main worktree and in a
    worktree of its own.
    """
    badge = Text(f'{entry.label:<12}', style='bold yellow' if entry.is_worktree else 'bold cyan')
    marker = Text('● ' if entry.is_current else '  ', style='green')
    prompt = Text()
    prompt.append(marker)
    prompt.append(badge)
    prompt.append(f'{entry.session.short_id}  ', style='bold')
    prompt.append(entry.session.preview[:60] or '(empty)', style='dim')
    return Option(prompt, id=entry.session.id)


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
