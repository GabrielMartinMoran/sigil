"""The Checkpoints value of the agents layer."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from src.core.agents.checkpoint import Checkpoint
from src.core.storage.blobs import ROADMAP, Blobs
from src.core.storage.database import Database
from src.core.storage.roadmap import plan_as_json

REASON_PROMPT = 'prompt'


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    import hashlib
    import uuid

    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:32]


class Checkpoints:
    """The rows that record where a session and its workspace stood."""

    def __init__(self, database: Database, blobs: Blobs) -> None:
        self._db = database
        self._blobs = blobs

    # --- reading -------------------------------------------------------------

    def of_session(self, session_id: str) -> list[Checkpoint]:
        rows = self._db.query(
            'SELECT * FROM checkpoints WHERE session_id = ? ORDER BY at',
            (session_id,),
        )
        return [_checkpoint_from(row) for row in rows]

    def of_turn(self, turn_id: str) -> Checkpoint | None:
        row = self._db.one('SELECT * FROM checkpoints WHERE turn_id = ?', (turn_id,))
        return None if row is None else _checkpoint_from(row)

    def get(self, checkpoint_id: str) -> Checkpoint | None:
        row = self._db.one('SELECT * FROM checkpoints WHERE id = ?', (checkpoint_id,))
        return None if row is None else _checkpoint_from(row)

    # --- writing -------------------------------------------------------------

    def _plan_before(self, execution_id: str, *, conn: sqlite3.Connection | None = None) -> str:
        """The plan the previous checkpoint of this execution recorded, or an empty one."""
        execute = (conn or self._db.connection).execute
        row = execute(
            'SELECT roadmap_blob FROM checkpoints WHERE execution_id = ? AND roadmap_blob IS NOT NULL '
            'ORDER BY at DESC LIMIT 1',
            (execution_id,),
        ).fetchone()
        return '[]' if row is None else self._blobs.data(int(row[0])).decode('utf-8')

    def take(
        self,
        session_id: str,
        execution_id: str,
        *,
        turn_id: str | None,
        reason: str = REASON_PROMPT,
        label: str = '',
        tree_commit: str | None = None,
        carry_plan: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> Checkpoint:
        """Records an instant, with the plan as it was.

        The plan goes into a blob because a cascade cannot restore it: a task belongs
        to an execution, so an item created by a dropped turn survives the cascade and
        only a snapshot can tell which items the turn added.

        ``carry_plan`` is what makes a turn's checkpoint mean *before that turn*. The
        per-turn checkpoint is written from a save that already contains the turn's
        messages and its plan changes, so snapshotting the plan "now" would restore a
        plan that includes the work being undone. Carrying the previous checkpoint's
        plan forward records the instant the user actually wants: the state before the
        turn ran. An undo checkpoint passes ``False``: it is written live, before
        anything is touched, so the plan it sees is the one to preserve.
        """
        checkpoint_id = _new_id()
        execute = (conn or self._db.connection).execute
        raw_plan = self._plan_before(execution_id, conn=conn) if carry_plan else plan_as_json(self._db, execution_id)
        roadmap_blob = self._blobs.put(raw_plan or '[]', kind=ROADMAP, media_type='application/json', conn=conn)
        execute(
            'INSERT INTO checkpoints '
            '(id, session_id, execution_id, turn_id, at, reason, label, tree_commit, roadmap_blob) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                checkpoint_id,
                session_id,
                execution_id,
                turn_id,
                _now(),
                reason,
                label,
                tree_commit,
                roadmap_blob,
            ),
        )
        return Checkpoint(
            id=checkpoint_id,
            session_id=session_id,
            execution_id=execution_id,
            turn_id=turn_id,
            at=_now(),
            reason=reason,
            label=label,
            tree_commit=tree_commit,
        )


def _checkpoint_from(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        id=str(row['id']),
        session_id=str(row['session_id']),
        execution_id=str(row['execution_id']),
        turn_id=None if row['turn_id'] is None else str(row['turn_id']),
        at=str(row['at']),
        reason=str(row['reason']),
        label=str(row['label'] or ''),
        tree_commit=None if row['tree_commit'] is None else str(row['tree_commit']),
    )
