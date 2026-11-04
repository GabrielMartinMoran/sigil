"""Panel lateral con el roadmap de la tarea actual."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static

from src.engine.roadmap.store import RoadmapStore

_STATUS_ORDER = ('in_progress', 'pending', 'blocked', 'done')
_STATUS_ICON = {'in_progress': '▶', 'pending': '○', 'blocked': '!', 'done': '✓'}
_STATUS_STYLE = {
    'in_progress': 'bold cyan',
    'pending': '',
    'blocked': 'bold yellow',
    'done': 'dim strike',
}
_STATUS_LABEL = {
    'in_progress': 'En progreso',
    'pending': 'Pendientes',
    'blocked': 'Bloqueadas',
    'done': 'Completadas',
}


class RoadmapPanel(Static):
    """Muestra las tareas pendientes y completadas."""

    def __init__(self, store: RoadmapStore | None = None, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._store = store

    def on_mount(self) -> None:
        self.refresh_from_store()

    def set_store(self, store: RoadmapStore | None) -> None:
        self._store = store
        self.refresh_from_store()

    def refresh_from_store(self) -> None:
        if self._store is None:
            self.display = False
            return
        self.display = True
        self.update(self._build_text())

    def _build_text(self) -> Text:
        text = Text()
        text.append('Roadmap\n', style='bold')
        if self._store is None:
            text.append('Sin roadmap.', style='dim')
            return text
        items = self._store.snapshot()
        if not items:
            text.append('Sin tareas todavía.', style='dim')
            return text
        for status in _STATUS_ORDER:
            group = [item for item in items if item.status == status]
            if not group:
                continue
            text.append(f'\n{_STATUS_LABEL[status]}\n', style='bold dim')
            for item in group:
                text.append(f' {_STATUS_ICON[status]} ', style=_STATUS_STYLE[status])
                text.append(item.title, style=_STATUS_STYLE[status])
                text.append('\n')
                if item.notes:
                    text.append(f'   {item.notes}\n', style='dim')
        return text
