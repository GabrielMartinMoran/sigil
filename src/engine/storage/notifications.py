"""SQLite mapping of the notification log; no transport or prompt policy here."""

from __future__ import annotations

import sqlite3

from src.engine.agents.tree.notifications import AgentNotification, Notifications
from src.engine.storage.database import Database


def _identity(entry: AgentNotification) -> tuple[str, str, str, str, float]:
    return entry.sender_id, entry.sender_name, entry.receiver_id, entry.text, entry.created_at


def _stored(conn: sqlite3.Connection, session_id: str) -> dict[int, AgentNotification]:
    cursor = conn.execute('SELECT * FROM agent_notifications WHERE session_id = ?', (session_id,))
    cursor.row_factory = sqlite3.Row
    entries = (_from_row(row) for row in cursor)
    return {entry.sequence: entry for entry in entries}


def _changes(
    conn: sqlite3.Connection, session_id: str, notifications: Notifications
) -> tuple[list[AgentNotification], list[int]]:
    stored = _stored(conn, session_id)
    additions: list[AgentNotification] = []
    acknowledgements: list[int] = []
    for entry in notifications.all():
        row = stored.get(entry.sequence)
        if row is None:
            additions.append(entry)
        elif _identity(row) != _identity(entry):
            raise ValueError(f'Notification {entry.id} conflicts with its stored identity or content.')
        elif entry.acknowledged and not row.acknowledged:
            acknowledgements.append(entry.sequence)
    return additions, acknowledgements


def save_notifications(conn: sqlite3.Connection, session_id: str, notifications: Notifications) -> None:
    """Validate first, then write only additions and new ACKs in the caller's transaction."""
    additions, acknowledgements = _changes(conn, session_id, notifications)
    conn.executemany(
        'INSERT INTO agent_notifications '
        '(session_id, sequence, sender_id, sender_name, receiver_id, content, created_at, acknowledged) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        ((session_id, entry.sequence, *_identity(entry), int(entry.acknowledged)) for entry in additions),
    )
    conn.executemany(
        'UPDATE agent_notifications SET acknowledged = 1 WHERE session_id = ? AND sequence = ?',
        ((session_id, sequence) for sequence in acknowledgements),
    )


def _from_row(row: sqlite3.Row) -> AgentNotification:
    return AgentNotification(
        sequence=int(row['sequence']),
        sender_id=str(row['sender_id']),
        sender_name=str(row['sender_name']),
        receiver_id=str(row['receiver_id']),
        text=str(row['content']),
        acknowledged=bool(row['acknowledged']),
        created_at=float(row['created_at']),
    )


def load_notifications(database: Database, session_id: str) -> Notifications:
    rows = database.query('SELECT * FROM agent_notifications WHERE session_id = ? ORDER BY sequence', (session_id,))
    return Notifications([_from_row(row) for row in rows])
