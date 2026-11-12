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


class RoadmapConflictError(RuntimeError):
    """The stored plan moved on since the caller read its revision.

    A conflict is a visible failure, not a reason to overwrite: the caller keeps its
    in-memory items and reports the collision instead of winning a race it cannot see.
    """

    def __init__(self, execution_id: str, expected: int, stored: int) -> None:
        self.execution_id = execution_id
        self.expected = expected
        self.stored = stored
        super().__init__(
            f'roadmap revision conflict for execution {execution_id}: '
            f'expected revision {expected}, stored revision {stored}'
        )


def read_items(database: Database, execution_id: str) -> list[TodoItem]:
    """The plan of an execution, in order."""
    with database.lock:
        return _read_items(database.connection, execution_id)


def read_plan(database: Database, execution_id: str) -> tuple[list[TodoItem], int]:
    """The plan and the revision it was read at, taken from one snapshot.

    Both SELECTs run inside one deferred read transaction. The lock keeps this
    process's threads out; the transaction is what keeps another process from
    committing between them and returning older items under a newer revision.
    """
    with database.lock:
        conn = database.connection
        if conn.in_transaction:
            # Already inside a caller's transaction: its snapshot is the coherent
            # one, and starting another would fail.
            return _read_items(conn, execution_id), _stored_revision(conn, execution_id)
        conn.execute('BEGIN DEFERRED')
        try:
            items = _read_items(conn, execution_id)
            revision = _stored_revision(conn, execution_id)
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            raise
        return items, revision


def _read_items(conn: sqlite3.Connection, execution_id: str) -> list[TodoItem]:
    rows = conn.execute(
        'SELECT id, title, status, notes, created_at, updated_at '
        'FROM roadmap_items WHERE execution_id = ? ORDER BY position',
        (execution_id,),
    ).fetchall()
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


def read_revision(database: Database, execution_id: str) -> int:
    """The revision of an execution's plan; never written means zero."""
    with database.lock:
        return _stored_revision(database.connection, execution_id)


def replace_items(
    database: Database,
    execution_id: str,
    items: list[TodoItem],
    *,
    expected_revision: int,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Makes the stored plan equal to ``items``, if it is still at ``expected_revision``.

    A whole replace and not a diff: a plan is a dozen rows, `position` has to be
    rewritten when the order changes, and the delete-then-insert is one transaction
    that cannot leave the list half-updated. The tools that change one item already
    changed it in memory first.

    The comparison and the increment live in that same transaction, so a stale
    snapshot raises :class:`RoadmapConflictError` before anything is touched. Returns
    the revision the new plan is stored at. A caller with an open transaction passes
    ``conn`` so no nested transaction is opened.
    """
    if conn is not None:
        return _replace(conn, execution_id, items, expected_revision)
    with database.transaction() as own:
        return _replace(own, execution_id, items, expected_revision)


def _replace(
    conn: sqlite3.Connection,
    execution_id: str,
    items: list[TodoItem],
    expected_revision: int,
) -> int:
    stored = _stored_revision(conn, execution_id)
    if stored != expected_revision:
        raise RoadmapConflictError(execution_id, expected_revision, stored)
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
    return _write_revision(conn, execution_id, stored + 1)


def delete_items(
    database: Database,
    execution_id: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Empties an execution's plan and moves its revision on.

    The revision row stays when the execution does: an empty plan is still a plan at
    a revision, and a reopened store must not mistake it for one that never moved.
    """
    if conn is not None:
        return _delete(conn, execution_id)
    with database.transaction() as own:
        return _delete(own, execution_id)


def _delete(conn: sqlite3.Connection, execution_id: str) -> int:
    stored = _stored_revision(conn, execution_id)
    conn.execute('DELETE FROM roadmap_items WHERE execution_id = ?', (execution_id,))
    if not _execution_exists(conn, execution_id):
        return stored
    return _write_revision(conn, execution_id, stored + 1)


def _execution_exists(conn: sqlite3.Connection, execution_id: str) -> bool:
    row = conn.execute('SELECT 1 FROM agent_executions WHERE id = ?', (execution_id,)).fetchone()
    return row is not None


def _stored_revision(conn: sqlite3.Connection, execution_id: str) -> int:
    row = conn.execute('SELECT revision FROM roadmap_revisions WHERE execution_id = ?', (execution_id,)).fetchone()
    return 0 if row is None else int(row[0])


def _write_revision(conn: sqlite3.Connection, execution_id: str, revision: int) -> int:
    conn.execute(
        'INSERT INTO roadmap_revisions (execution_id, revision) VALUES (?, ?) '
        'ON CONFLICT(execution_id) DO UPDATE SET revision = excluded.revision',
        (execution_id, revision),
    )
    return revision


def plan_as_json(database: Database, execution_id: str) -> str:
    """The plan as JSON, for the checkpoint a rollback restores from."""
    import json

    return json.dumps(
        [item.model_dump() for item in read_items(database, execution_id)],
        ensure_ascii=False,
        separators=(',', ':'),
    )


def restore_plan_from_json(database: Database, execution_id: str, raw: str) -> int:
    """Puts a plan back from the JSON a checkpoint kept. Returns how many tasks.

    A restore is a cooperative write: it reads the revision inside the same
    transaction it writes in, so it moves the plan forward instead of colliding with
    a stale snapshot. Values that are not a plan leave the stored one untouched.
    """
    import json

    try:
        data = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(data, list):
        return 0
    items = [TodoItem(**entry) for entry in data if isinstance(entry, dict)]
    with database.transaction() as conn:
        replace_items(
            database,
            execution_id,
            items,
            expected_revision=_stored_revision(conn, execution_id),
            conn=conn,
        )
    return len(items)


__all__ = [
    'RoadmapConflictError',
    'delete_items',
    'plan_as_json',
    'read_items',
    'read_plan',
    'read_revision',
    'replace_items',
    'restore_plan_from_json',
]
