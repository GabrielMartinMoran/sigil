"""Panel lateral con el roadmap de la tarea actual."""

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
    """Muestra el roadmap completo como una sola lista, en orden de plan."""

    def __init__(self, store: RoadmapStore | None = None, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._store = store
        self._shown = True

    def on_mount(self) -> None:
        self.refresh_from_store()

    @property
    def shown(self) -> bool:
        """Si el usuario pidió ver el panel, más allá de si hay roadmap.

        No se llama ``visible`` porque Textual ya usa ese nombre para la regla
        CSS ``visibility``, que reserva espacio en vez de liberarlo.
        """
        return self._shown

    def set_shown(self, shown: bool) -> None:
        """Muestra u oculta el panel y lo redibuja."""
        self._shown = shown
        self.refresh_from_store()

    def set_store(self, store: RoadmapStore | None) -> None:
        self._store = store
        self.refresh_from_store()

    def refresh_from_store(self) -> None:
        # El toggle del usuario manda: un roadmap que cambia no vuelve a mostrar
        # un panel que el usuario ocultó.
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
