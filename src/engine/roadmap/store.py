"""Roadmap of one execution, with persistence and UI notification.

The plan is rows keyed by execution. That removed two things the file version
needed: there is no directory to hash and no file per workspace, so switching
workspace no longer moves or reloads anything, and the root's plan is keyed by the
root's execution id instead of a sentinel.

Persistence is still best-effort: if it fails, the in-memory state keeps working and
the reason stays in ``persistence_error``. The one failure worth naming is a plan
written for an execution the database has not seen yet — a subagent whose node was
added to the tree but not saved. ``flush`` is the way out: the app passes a callable
that persists the session, which is what creates the execution row.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from src.engine.roadmap.models.todo_item import TodoItem, now_iso
from src.engine.roadmap.models.todo_status import TODO_STATUSES, TodoStatus
from src.engine.storage.database import Database, database_path
from src.engine.storage.roadmap import (
    RoadmapConflictError,
    delete_items,
    read_plan,
    replace_items,
)


class RoadmapStore:
    """Roadmap of one execution, with persistence and UI notification.

    ``bind`` re-points the store to another execution without recreating the object,
    so the agent's tool always writes to the one in effect.
    """

    def __init__(
        self,
        execution_id: str | None = None,
        *,
        base_dir: str | Path | None = None,
        flush: Callable[[], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._items: list[TodoItem] = []
        self._revision = 0
        self.persistence_error: str | None = None
        self._database = Database.at(database_path(base_dir))
        self._execution_id = execution_id
        self._flush = flush
        self._load()

    @property
    def execution_id(self) -> str | None:
        return self._execution_id

    @property
    def database(self) -> Database:
        return self._database

    def bind(self, execution_id: str | None) -> None:
        """Re-points the store to the plan of ``execution_id`` and reloads it."""
        with self._lock:
            if execution_id == self._execution_id:
                return
            self._execution_id = execution_id
            self._items = []
            self.persistence_error = None
            self._load()
        self._notify()

    # --- reading -------------------------------------------------------------

    def snapshot(self) -> list[TodoItem]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._items]

    def counts(self) -> dict[str, int]:
        with self._lock:
            counts = {status: 0 for status in TODO_STATUSES}
            for item in self._items:
                counts[item.status] += 1
            return counts

    # --- writing -------------------------------------------------------------

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

    def reload(self) -> None:
        """Re-reads the plan from its rows and notifies.

        A rollback rewrites the rows behind the store's back; without this the panel
        would keep painting a plan that no longer exists.
        """
        with self._lock:
            self._load()
        self._notify()

    # --- listeners -----------------------------------------------------------

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
            except Exception:  # noqa: BLE001, S110 - the UI must not break the state
                pass

    # --- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self._execution_id:
            self._items = []
            self._revision = 0
            return
        try:
            self._items, self._revision = read_plan(self._database, self._execution_id)
            self.persistence_error = None
        except sqlite3.Error as exc:
            self._items = []
            self._revision = 0
            self.persistence_error = str(exc)

    def _save_locked(self) -> None:
        if not self._execution_id:
            self.persistence_error = None
            return
        try:
            self._revision = replace_items(
                self._database,
                self._execution_id,
                self._items,
                expected_revision=self._revision,
            )
            self.persistence_error = None
        except RoadmapConflictError as exc:
            # Another store wrote first. The dirty items stay in memory: overwriting
            # the winner would lose its work, and clearing ours would lose the user's.
            self.persistence_error = str(exc)
        except sqlite3.IntegrityError as exc:
            # The execution is not a row yet: a subagent whose node lives only in
            # the tree. Ask the app to persist the session, which creates it, and
            # try once more. The list is written whole, so nothing is lost either
            # way.
            self.persistence_error = str(exc)
            if self._flush is None:
                return
            try:
                self._flush()
                self._revision = replace_items(
                    self._database,
                    self._execution_id,
                    self._items,
                    expected_revision=self._revision,
                )
                self.persistence_error = None
            except Exception as retry:  # noqa: BLE001 - reported, not raised
                self.persistence_error = str(retry)
        except sqlite3.Error as exc:
            self.persistence_error = str(exc)

    # --- maintenance ---------------------------------------------------------

    def forget(self) -> None:
        """Deletes the plan of this execution. Called when the execution is gone."""
        if not self._execution_id:
            return
        with self._lock:
            self._items = []
            try:
                self._revision = delete_items(self._database, self._execution_id)
                self.persistence_error = None
            except sqlite3.Error as exc:
                self.persistence_error = str(exc)


__all__ = ['RoadmapStore']
