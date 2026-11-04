"""Estado del roadmap de la tarea actual."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

TodoStatus = Literal['pending', 'in_progress', 'done', 'blocked']
TODO_STATUSES: tuple[TodoStatus, ...] = ('pending', 'in_progress', 'done', 'blocked')


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _cwd_hash(cwd: Path) -> str:
    return hashlib.sha256(str(cwd.resolve()).encode('utf-8')).hexdigest()[:16]


class TodoItem(BaseModel):
    """Una tarea del roadmap."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = Field(min_length=1)
    status: TodoStatus = 'pending'
    notes: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class RoadmapStore:
    """Lista de tareas con persistencia y notificación a la UI.

    La persistencia es best-effort: si falla, el estado en memoria sigue
    funcionando y el error queda en ``persistence_error``.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._items: list[TodoItem] = []
        self.persistence_error: str | None = None
        self._path = Path(path) if path is not None else self._default_path()
        self._load()

    @staticmethod
    def _default_path() -> Path:
        base = Path.home() / '.sigil' / 'roadmaps'
        return base / f'{_cwd_hash(Path.cwd())}.json'

    @property
    def path(self) -> Path:
        return self._path

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
                data['updated_at'] = _now()
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
        try:
            raw = self._path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return
        except OSError as exc:
            self.persistence_error = str(exc)
            return
        try:
            data = json.loads(raw)
            self._items = [TodoItem(**item) for item in data.get('items', [])]
        except (ValueError, TypeError) as exc:
            self.persistence_error = f'Could not parse {self._path}: {exc}'

    def _save_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {'items': [item.model_dump() for item in self._items]}
            self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
            self.persistence_error = None
        except OSError as exc:
            self.persistence_error = str(exc)


__all__ = ['TODO_STATUSES', 'RoadmapStore', 'TodoItem', 'TodoStatus']
