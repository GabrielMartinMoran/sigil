"""Side panel with the roadmap of the current task."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static

from src.engine.roadmap.store import RoadmapStore

_STATUS_ICON = {'in_progress': '▶', 'pending': '○', 'blocked': '!', 'done': '✓'}
_STATUS_STYLE = {
    'in_progress': 'bold blue',
    'pending': '',
    'blocked': 'bold yellow',
    'done': 'green strike',
}
_FALLBACK_ICON = '·'


class RoadmapPanel(Static):
    """Shows the whole roadmap as a single list, in plan order."""

    def __init__(self, store: RoadmapStore | None = None, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._store = store
        self._shown = True

    def on_mount(self) -> None:
        self.refresh_from_store()

    @property
    def shown(self) -> bool:
        """Whether the user asked to see the panel, regardless of whether there is a roadmap.

        It is not called ``visible`` because Textual already uses that name for the
        CSS ``visibility`` rule, which reserves space instead of releasing it.
        """
        return self._shown

    def set_shown(self, shown: bool) -> None:
        """Shows or hides the panel and redraws it."""
        self._shown = shown
        self.refresh_from_store()

    def set_store(self, store: RoadmapStore | None) -> None:
        self._store = store
        self.refresh_from_store()

    def refresh_from_store(self) -> None:
        # The user toggle wins: a changing roadmap does not show again
        # a panel the user hid.
        if self._store is None or not self._shown:
            self.display = False
            return
        self.display = True
        self.update(self._build_text())

    def _build_text(self) -> Text:
        text = Text()
        text.append('Roadmap\n', style='bold')
        if self._store is None:
            text.append('No roadmap.', style='dim')
            return text
        items = self._store.snapshot()
        if not items:
            text.append('No tasks yet.', style='dim')
            return text

        for item in items:
            style = _STATUS_STYLE.get(item.status, '')
            icon = _STATUS_ICON.get(item.status, _FALLBACK_ICON)
            text.append(f' {icon} ', style=style)
            text.append(item.title, style=style)
            text.append('\n')
            if item.notes:
                text.append(f'   {item.notes}\n', style='dim')
        return text
