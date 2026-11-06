"""Roadmap rows: one plan per execution.

A plan cannot be derived from a transcript — a task can be re-opened without a new
tool call — so unlike the loaded skills it *is* stored. What changed is where: rows
keyed by execution, instead of one JSON file per session and node.

The root's plan is the root execution's plan. There is no sentinel and no `NULL` in
a primary key: the root has an id like any other execution.
"""

from __future__ import annotations

import sqlite3

from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.storage.database import Database


def read_items(database: Database, execution_id: str) -> list[TodoItem]:
    """The plan of an execution, in order."""
    rows = database.query(
        'SELECT id, title, status, notes, created_at, updated_at '
        'FROM roadmap_items WHERE execution_id = ? ORDER BY position',
        (execution_id,),
    )
    return [
        TodoItem(
            id=str(row['id']),
            title=str(row['title']),
            status=str(row['status']),  # type: ignore[arg-type]
            notes=None if row['notes'] is None else str(row['notes']),
            created_at=str(row['created_at']),
            updated_at=str(row['updated_at']),
        )
        for row in rows
    ]


def replace_items(
    database: Database,
    execution_id: str,
    items: list[TodoItem],
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Makes the stored plan equal to ``items``.

    A whole replace and not a diff: a plan is a dozen rows, `position` has to be
    rewritten when the order changes, and the delete-then-insert is one transaction
    that cannot leave the list half-updated. The tools that change one item already
    changed it in memory first.
    """
    if conn is not None:
        _replace(conn, execution_id, items)
        return
    with database.transaction() as own:
        _replace(own, execution_id, items)


def _replace(conn: sqlite3.Connection, execution_id: str, items: list[TodoItem]) -> None:
    conn.execute('DELETE FROM roadmap_items WHERE execution_id = ?', (execution_id,))
    conn.executemany(
        'INSERT INTO roadmap_items '
        '(execution_id, id, position, title, status, notes, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [
            (
                execution_id,
                item.id,
                position,
                item.title,
                str(item.status),
                item.notes,
                item.created_at,
                item.updated_at,
            )
            for position, item in enumerate(items)
        ],
    )


def delete_items(database: Database, execution_id: str) -> None:
    """Forgets an execution's plan. Called when the execution is gone for good."""
    database.execute('DELETE FROM roadmap_items WHERE execution_id = ?', (execution_id,))


def plan_as_json(database: Database, execution_id: str) -> str:
    """The plan as JSON, for the checkpoint a rollback restores from."""
    import json

    return json.dumps(
        [item.model_dump() for item in read_items(database, execution_id)],
        ensure_ascii=False,
        separators=(',', ':'),
    )


def restore_plan_from_json(database: Database, execution_id: str, raw: str) -> int:
    """Puts a plan back from the JSON a checkpoint kept. Returns how many tasks."""
    import json

    try:
        data = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(data, list):
        return 0
    items = [TodoItem(**entry) for entry in data if isinstance(entry, dict)]
    replace_items(database, execution_id, items)
    return len(items)


__all__ = [
    'delete_items',
    'plan_as_json',
    'read_items',
    'replace_items',
    'restore_plan_from_json',
]
