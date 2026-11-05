"""Side panel with the roadmap of the current task.

It is the bottom box of the column, and the only one that grows: the diagnostics
above are as tall as their content, and this one takes the rest. The plan inside it
scrolls, so a list of thirty items stays inside its box instead of pushing the
composer off the screen or squeezing the model name out of view.

The heading stays put while the items move. It is the label of the box, and a label
that scrolls away leaves a column of checkmarks with nothing saying what they are.

The plan is a list in plan order, not grouped by status: the icon and the color
already say what each item is, and grouping would reorder the plan every time the
agent checked something off — exactly when the user is reading it.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from src.engine.roadmap.store import RoadmapStore
from src.ui.palette import color

STATUS_ICON = {'in_progress': '▶', 'pending': '○', 'blocked': '!', 'done': '✓'}
STATUS_STYLE = {
    'in_progress': 'bold blue',
    'pending': '',
    'blocked': 'bold yellow',
    'done': 'green strike',
}
FALLBACK_ICON = '·'


class RoadmapPanel(Vertical):
    """The roadmap of the session: a fixed heading over a scrolling list of items."""

    def __init__(self, store: RoadmapStore | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._store = store
        self._shown = True

    def compose(self) -> ComposeResult:
        yield Static(Text('Roadmap'), id='roadmap-title')
        with VerticalScroll(id='roadmap-scroll'):
            yield Static(Text(''), id='roadmap-body')

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
        """Repaints after the roadmap changed, or after a theme change.

        The user toggle wins: a changing roadmap does not bring back a panel the
        user hid.
        """
        if not self._shown:
            self.display = False
            return
        self.display = True
        self.query_one('#roadmap-title', Static).update(Text('Roadmap', style=f'bold {color(self, "sidebar-section")}'))
        self.query_one('#roadmap-body', Static).update(self._build_items())

    def refresh_palette(self) -> None:
        """Rebuilds the text after a theme change: the heading keeps the old color."""
        self.refresh_from_store()

    @property
    def text(self) -> str:
        """The whole panel as plain text, for tests and diagnostics."""
        return f'Roadmap\n{self._build_items().plain}'

    def _build_items(self) -> Text:
        """The list itself, without the heading: this is what scrolls."""
        text = Text()
        if self._store is None:
            text.append('No roadmap.', style='dim')
            return text
        items = self._store.snapshot()
        if not items:
            text.append('No tasks yet.', style='dim')
            return text

        for index, item in enumerate(items):
            style = STATUS_STYLE.get(item.status, '')
            icon = STATUS_ICON.get(item.status, FALLBACK_ICON)
            if index:
                text.append('\n')
            text.append(f' {icon} ', style=style)
            text.append(item.title, style=style)
            if item.notes:
                text.append(f'\n   {item.notes}', style='dim')
        return text
