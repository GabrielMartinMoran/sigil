"""Durable, per-session input records and their validated state transitions."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from src.core.storage.accept import _record
from src.core.storage.accepted_input import AcceptedInput, InputState
from src.core.storage.database import Database

OPEN_INPUTS_SQL = "SELECT 1 FROM accepted_inputs WHERE state IN ('accepted', 'claimed', 'delivered') LIMIT 1"
"""The runtime's ``is anything still open`` question, named so its plan can be tested.

The predicate is the whole of it, and it matches ``accepted_inputs_open`` exactly — the
partial index over the three open states, added in schema step 8. That table only
grows and this runs on a timer, so the match is what keeps the poll a lookup instead
of a scan.
"""

_TRANSITIONS = {
    'accepted': {'claimed', 'stopped', 'interrupted'},
    'claimed': {'stopped', 'interrupted'},
    'delivered': {'finished', 'stopped', 'interrupted'},
    'finished': set(),
    'stopped': set(),
    'interrupted': set(),
}


class AcceptedInputs:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, session_id: str) -> list[AcceptedInput]:
        return [
            _record(row)
            for row in self.database.query(
                'SELECT * FROM accepted_inputs WHERE session_id = ? ORDER BY sequence', (session_id,)
            )
        ]

    def claim_next(self, session_id: str, *, attempt_id: str) -> AcceptedInput | None:
        """Claims the oldest accepted input of a session, once.

        The selection and the transition share one transaction, so two consumers
        cannot both claim the same row.
        """
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT request_id FROM accepted_inputs WHERE session_id = ? AND state = 'accepted' "
                'ORDER BY sequence LIMIT 1',
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return self.transition(session_id, str(row[0]), 'claimed', attempt_id=attempt_id, conn=conn)

    def has_open(self) -> bool:
        """Database-wide: whether any input anywhere is still non-terminal.

        The service serves one database, not one session, so a row another client
        accepted is work this process must not stop in the middle of. Read-only and
        cheap enough to ask on a timer: it stops at the first open row.
        """
        return self.database.one(OPEN_INPUTS_SQL) is not None

    def interrupt_open(self, session_id: str | None = None) -> int:
        """Interrupt non-terminal inputs globally or for one session."""
        now = datetime.now(UTC).isoformat()
        query = (
            "UPDATE accepted_inputs SET state = 'interrupted', updated_at = ? "
            "WHERE state IN ('accepted', 'claimed', 'delivered')"
        )
        params = (now,)
        if session_id is not None:
            query += ' AND session_id = ?'
            params = (now, session_id)
        with self.database.transaction() as conn:
            cursor = conn.execute(query, params)
        return int(cursor.rowcount)

    def stop_open(self, session_id: str, *, keep: Sequence[str] = ()) -> int:
        """Stops the inputs of a session that were never delivered.

        ``keep`` names requests that are already handed to a live run. They are not
        stopped here: the run decides whether they were delivered or not.
        """
        now = datetime.now(UTC).isoformat()
        placeholders = ', '.join('?' for _ in keep)
        exclusion = f' AND request_id NOT IN ({placeholders})' if keep else ''
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "UPDATE accepted_inputs SET state = 'stopped', updated_at = ? "
                "WHERE session_id = ? AND state IN ('accepted', 'claimed')" + exclusion,
                (now, session_id, *keep),
            )
        return int(cursor.rowcount)

    def transition(
        self,
        session_id: str,
        request_id: str,
        state: InputState,
        *,
        attempt_id: str | None = None,
        library_run_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> AcceptedInput:
        if state == 'delivered':
            raise ValueError('delivered is only written by confirm_delivery with the transcript')
        if conn is None:
            with self.database.transaction() as transaction:
                return self.transition(
                    session_id,
                    request_id,
                    state,
                    attempt_id=attempt_id,
                    library_run_id=library_run_id,
                    conn=transaction,
                )
        row = conn.execute(
            'SELECT * FROM accepted_inputs WHERE session_id = ? AND request_id = ?', (session_id, request_id)
        ).fetchone()
        if row is None:
            raise LookupError('Accepted input not found')
        if state not in _TRANSITIONS[row['state']]:
            raise ValueError('Invalid input state transition')
        now = datetime.now(UTC).isoformat()
        conn.execute(
            'UPDATE accepted_inputs SET state = ?, attempt_id = COALESCE(?, attempt_id), '
            'library_run_id = COALESCE(?, library_run_id), updated_at = ? WHERE session_id = ? AND request_id = ?',
            (state, attempt_id, library_run_id, now, session_id, request_id),
        )
        return _record(
            conn.execute(
                'SELECT * FROM accepted_inputs WHERE session_id = ? AND request_id = ?', (session_id, request_id)
            ).fetchone()
        )
