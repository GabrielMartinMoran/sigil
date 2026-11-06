"""Maintenance: what keeps the database from growing without a ceiling.

Three jobs, all of them safe to run while the app is open and all of them cheap enough
to run on idle:

- **Collect unreferenced blobs.** A skill loaded and then rolled back leaves its
  instruction block with no message pointing at it.
- **Reclaim space.** `PRAGMA optimize` for the query planner, an incremental vacuum
  for the pages a truncation left behind.
- **Export a session.** The backup that does not depend on being able to read an older
  format, which is what this design traded the import for.

The collector is the only one that deletes, and it is deliberately narrow: it only
touches blobs of kind `instructions`. An `image` blob is referenced from inside a
message payload, not from a column, so the collector cannot see that reference and
would delete pixels `view_image` still needs; and `roadmap`/`abandoned` blobs are
referenced from `checkpoints`, which a rollback needs to restore a plan and to undo
itself.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.engine.storage.blobs import Blobs
from src.engine.storage.database import Database


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """What a pass actually did."""

    blobs_collected: int
    checkpoints_dropped: int
    bytes_before: int
    bytes_after: int

    @property
    def reclaimed(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)


def _size(database: Database) -> int:
    try:
        return Path(database.path).stat().st_size
    except OSError:
        return 0


def collect_blobs(database: Database, blobs: Blobs) -> int:
    """Deletes instruction blobs nothing points at any more."""
    with database.transaction() as conn:
        removed = conn.execute(
            "DELETE FROM blobs WHERE kind = 'instructions' "
            'AND id NOT IN (SELECT instructions_id FROM messages WHERE instructions_id IS NOT NULL)'
        ).rowcount
    blobs.forget_cache()
    return int(removed or 0)


def drop_old_checkpoints(database: Database, blobs: Blobs, *, keep: int = 50) -> int:
    """Keeps the newest ``keep`` checkpoints of each session, and their undo.

    A checkpoint costs a commit object, a plan blob and a row. Fifty per session is
    months of work, and the `before_rollback` ones are always kept: they are the way
    back from a rollback, and dropping one silently removes the undo of an operation
    the user may still regret.
    """
    dropped = 0
    sessions = [row['session_id'] for row in database.query('SELECT DISTINCT session_id FROM checkpoints')]
    for session_id in sessions:
        rows = database.query(
            "SELECT id FROM checkpoints WHERE session_id = ? AND reason != 'before_rollback' ORDER BY at DESC",
            (session_id,),
        )
        stale = [str(row['id']) for row in rows[keep:]]
        if not stale:
            continue
        placeholders = ','.join('?' for _ in stale)
        with database.transaction() as conn:
            dropped += conn.execute(f'DELETE FROM checkpoints WHERE id IN ({placeholders})', tuple(stale)).rowcount or 0
    return dropped


def optimize(database: Database) -> None:
    """Reclaims pages and refreshes what the planner knows about the data."""
    database.execute('PRAGMA optimize')
    try:
        database.execute('PRAGMA incremental_vacuum')
    except sqlite3.Error:  # pragma: no cover - auto_vacuum off in an old file
        pass


def run(database: Database, blobs: Blobs, *, keep_checkpoints: int = 50) -> MaintenanceReport:
    """One pass of everything that keeps the database bounded."""
    before = _size(database)
    collected = collect_blobs(database, blobs)
    dropped = drop_old_checkpoints(database, blobs, keep=keep_checkpoints)
    optimize(database)
    return MaintenanceReport(
        blobs_collected=collected,
        checkpoints_dropped=dropped,
        bytes_before=before,
        bytes_after=_size(database),
    )


def export_session(database: Database, blobs: Blobs, session_id: str) -> str:
    """A session as JSON: the row, its executions, its turns and its transcript.

    The backup that does not depend on reading an older format. It is not the file
    the app used to write — there is no importer for either shape — it is a document
    a person can read with `jq` if the database is ever lost.
    """
    session = database.one('SELECT * FROM sessions WHERE id = ?', (session_id,))
    if session is None:
        raise KeyError(f'No session {session_id!r}')
    executions = database.query(
        'SELECT * FROM agent_executions WHERE session_id = ? ORDER BY depth, started_at', (session_id,)
    )
    payload: dict[str, object] = {
        'id': str(session['id']),
        'title': session['title'],
        'created_at': str(session['created_at']),
        'updated_at': str(session['updated_at']),
        'profile': None,
        'executions': [],
    }
    for execution in executions:
        turns = database.query(
            'SELECT * FROM turns WHERE execution_id = ? ORDER BY started_at', (str(execution['id']),)
        )
        messages = database.query('SELECT * FROM messages WHERE execution_id = ? ORDER BY seq', (str(execution['id']),))
        entry = {
            'id': str(execution['id']),
            'parent_id': execution['parent_id'],
            'name': str(execution['name']),
            'role': str(execution['role']),
            'profile': str(execution['profile']),
            'status': str(execution['status']),
            'brief': str(execution['brief']),
            'result': execution['result'],
            'turns': [
                {
                    'id': str(turn['id']),
                    'origin': str(turn['origin']),
                    'started_at': str(turn['started_at']),
                    'outcome': turn['outcome'],
                    'steps': int(turn['steps']),
                    'cost': turn['cost'],
                }
                for turn in turns
            ],
            'messages': [
                {
                    'seq': int(message['seq']),
                    'turn_id': str(message['turn_id']),
                    'kind': str(message['kind']),
                    'payload': json.loads(str(message['payload'])),
                    'instructions': None
                    if message['instructions_id'] is None
                    else blobs.text(int(message['instructions_id'])),
                }
                for message in messages
            ],
        }
        if execution['parent_id'] is None:
            payload['profile'] = str(execution['profile'])
        payload['executions'].append(entry)  # type: ignore[union-attr]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def backup(database: Database, destination: str | Path) -> Path:
    """A consistent copy of the whole database, taken with SQLite's own mechanism."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    database.execute('VACUUM INTO ?', (str(target),))
    return target


__all__ = [
    'MaintenanceReport',
    'backup',
    'collect_blobs',
    'drop_old_checkpoints',
    'export_session',
    'optimize',
    'run',
]
