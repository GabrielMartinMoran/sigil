"""The side column: the diagnostics box on top, the roadmap at the bottom.

The column exists as a widget of its own for one reason: it is the thing `/sidebar`
shows and hides. Hiding the two boxes separately would mean two copies of the same
flag that can disagree, and a session where the diagnostics are visible and the plan
is not has no reading that makes sense.

The heights are split by the stylesheet: the diagnostics are as tall as their
content up to a share of the column, and the roadmap takes the rest. Each box
scrolls inside itself.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical

from src.engine.roadmap.store import RoadmapStore
from src.ui.widgets.roadmap_panel import RoadmapPanel
from src.ui.widgets.sidebar_panel import SidebarPanel


class SidebarColumn(Vertical):
    """Both side boxes, with the one visibility flag that hides them together."""

    def __init__(self, store: RoadmapStore | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._store = store
        self._shown = True

    def compose(self) -> ComposeResult:
        yield SidebarPanel(id='sidebar-panel')
        yield RoadmapPanel(self._store, id='roadmap')

    @property
    def shown(self) -> bool:
        """Whether the user asked to see the column, regardless of its content.

        It is not called ``visible`` because Textual already uses that name for the
        CSS ``visibility`` rule, which reserves space instead of releasing it.
        """
        return self._shown

    def set_shown(self, shown: bool) -> None:
        """Shows or hides the whole column.

        It is not `display = shown` alone: a hidden column must not come back on its
        own when the roadmap changes, so the flag is what `refresh_from_store` reads
        (see ``RoadmapPanel``).
        """
        self._shown = shown
        self.display = shown
        self.query_one(RoadmapPanel).set_shown(shown)

    def set_store(self, store: RoadmapStore | None) -> None:
        """Points the roadmap at the session's plan."""
        self._store = store
        self.query_one(RoadmapPanel).set_store(store)
