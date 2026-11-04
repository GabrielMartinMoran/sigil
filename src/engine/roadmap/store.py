"""Roadmap de una sesión, con persistencia y notificación a la UI."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from src.engine.roadmap.models.todo_item import TodoItem, now_iso
from src.engine.roadmap.models.todo_status import TODO_STATUSES, TodoStatus
from src.engine.roadmap.persistence import default_directory, load_items, path_for, save_items


class RoadmapStore:
    """Roadmap de una sesión, con persistencia y notificación a la UI.

    Cada sesión tiene su propio archivo: el roadmap nace vacío con la sesión,
    avanza con ella y se recupera al cargarla. ``bind_session`` reapunta el
    store al roadmap de otra sesión sin recrear el objeto, de modo que la tool
    del agente siempre escribe en la sesión activa.

    La persistencia es best-effort: si falla, el estado en memoria sigue
    funcionando y el error queda en ``persistence_error``.
    """

    def __init__(self, directory: str | Path | None = None, session_id: str | None = None) -> None:
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._items: list[TodoItem] = []
        self.persistence_error: str | None = None
        self._directory = Path(directory) if directory is not None else default_directory()
        self._session_id = session_id
        self._path = path_for(self._directory, session_id)
        self._load()

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def path(self) -> Path | None:
        return self._path

    def bind_session(self, session_id: str | None) -> None:
        """Reapunta el store al roadmap de ``session_id`` y lo recarga."""
        with self._lock:
            if session_id == self._session_id:
                return
            self._session_id = session_id
            self._path = path_for(self._directory, session_id)
            self._items = []
            self.persistence_error = None
            self._load()
        self._notify()

    def snapshot(self) -> list[TodoItem]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._items]

    def counts(self) -> dict[str, int]:
        with self._lock:
            counts = {status: 0 for status in TODO_STATUSES}
            for item in self._items:
                counts[item.status] += 1
            return counts

    def set_items(self, items: list[TodoItem]) -> list[TodoItem]:
        with self._lock:
            self._items = [item.model_copy(deep=True) for item in items]
            self._save_locked()
        self._notify()
        return self.snapshot()

    def add_items(self, items: list[TodoItem]) -> list[TodoItem]:
        with self._lock:
            self._items.extend(item.model_copy(deep=True) for item in items)
            self._save_locked()
        self._notify()
        return self.snapshot()

    def update_item(
        self,
        item_id: str,
        *,
        title: str | None = None,
        status: TodoStatus | None = None,
        notes: str | None = None,
    ) -> TodoItem:
        with self._lock:
            updated: TodoItem | None = None
            for index, item in enumerate(self._items):
                if item.id != item_id:
                    continue
                data = item.model_dump()
                if title is not None:
                    data['title'] = title
                if status is not None:
                    data['status'] = status
                if notes is not None:
                    data['notes'] = notes
                data['updated_at'] = now_iso()
                updated = TodoItem(**data)
                self._items[index] = updated
                self._save_locked()
                break
            if updated is None:
                raise KeyError(f'No roadmap item with id {item_id!r}.')
        self._notify()
        return updated

    def remove_item(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.id != item_id]
            removed = len(self._items) != before
            if removed:
                self._save_locked()
        if removed:
            self._notify()
        return removed

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save_locked()
        self._notify()

    def add_listener(self, listener: Callable[[], None]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001, S110 - la UI no debe romper el estado
                pass

    def _load(self) -> None:
        items, error = load_items(self._path)
        self._items = items
        self.persistence_error = error

    def _save_locked(self) -> None:
        self.persistence_error = save_items(self._path, self._items)
