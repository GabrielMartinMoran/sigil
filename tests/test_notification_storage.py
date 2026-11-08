"""Notification checkpoint writes are incremental and ACK is monotonic."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace

import pytest

from src.engine.agents.tree.notifications import AgentNotification, Notifications
from src.engine.storage.notifications import save_notifications


@pytest.fixture()
def connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(':memory:')
    connection.execute(
        'CREATE TABLE agent_notifications ('
        'session_id TEXT, sequence INTEGER, sender_id TEXT, sender_name TEXT, receiver_id TEXT, '
        'content TEXT, acknowledged INTEGER, created_at REAL, PRIMARY KEY(session_id, sequence))'
    )
    try:
        yield connection
    finally:
        connection.close()


def notice(sequence: int = 1) -> AgentNotification:
    return AgentNotification(sequence, 'sender', 'worker', 'receiver', f'report {sequence}', created_at=1.0)


def writes(statements: list[str]) -> list[str]:
    return [
        statement for statement in statements if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
    ]


def test_should_issue_no_writes_when_checkpoint_has_no_new_state(connection: sqlite3.Connection) -> None:
    log = Notifications([notice()])
    save_notifications(connection, 'session', log)
    statements: list[str] = []
    connection.set_trace_callback(statements.append)

    save_notifications(connection, 'session', log)

    assert writes(statements) == []


def test_should_write_only_delta_when_one_notice_arrives_and_one_is_acked(connection: sqlite3.Connection) -> None:
    original = notice()
    save_notifications(connection, 'session', Notifications([original]))
    statements: list[str] = []
    connection.set_trace_callback(statements.append)

    save_notifications(connection, 'session', Notifications([replace(original, acknowledged=True), notice(2)]))

    assert len(writes(statements)) == 2
    assert connection.execute(
        'SELECT sequence, acknowledged FROM agent_notifications ORDER BY sequence'
    ).fetchall() == [(1, 1), (2, 0)]


def test_should_not_revoke_ack_when_stale_checkpoint_is_saved(connection: sqlite3.Connection) -> None:
    original = notice()
    save_notifications(connection, 'session', Notifications([replace(original, acknowledged=True)]))

    save_notifications(connection, 'session', Notifications([original]))

    assert connection.execute('SELECT acknowledged FROM agent_notifications').fetchone()[0] == 1


def test_should_reject_conflicting_identity_instead_of_silently_losing_content(connection: sqlite3.Connection) -> None:
    original = notice()
    save_notifications(connection, 'session', Notifications([original]))

    with pytest.raises(ValueError, match='n-1'):
        save_notifications(connection, 'session', Notifications([replace(original, text='different report')]))


def test_should_validate_whole_snapshot_before_writing_when_later_entry_conflicts(
    connection: sqlite3.Connection,
) -> None:
    original = notice(2)
    save_notifications(connection, 'session', Notifications([original]))
    log = Notifications([notice(1), replace(original, text='conflicting')])
    statements: list[str] = []
    connection.set_trace_callback(statements.append)

    with pytest.raises(ValueError, match='n-2'):
        save_notifications(connection, 'session', log)

    assert writes(statements) == []


def test_should_keep_session_ids_independent_when_sequences_overlap(connection: sqlite3.Connection) -> None:
    save_notifications(connection, 'first', Notifications([notice()]))
    save_notifications(connection, 'second', Notifications([replace(notice(), text='another session')]))

    assert connection.execute('SELECT session_id, content FROM agent_notifications ORDER BY session_id').fetchall() == [
        ('first', 'report 1'),
        ('second', 'another session'),
    ]


def test_should_rollback_notification_and_ack_when_transaction_fails(connection: sqlite3.Connection) -> None:
    original = notice()
    save_notifications(connection, 'session', Notifications([original]))
    connection.commit()

    with pytest.raises(RuntimeError), connection:
        save_notifications(connection, 'session', Notifications([replace(original, acknowledged=True), notice(2)]))
        raise RuntimeError('failed session save')

    assert connection.execute('SELECT sequence, acknowledged FROM agent_notifications').fetchall() == [(1, 0)]
