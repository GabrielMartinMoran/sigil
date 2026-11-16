"""The confirm_delivery function of the storage layer."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from src.core.storage.delivery_confirmation import DeliveryConfirmation
from src.core.storage.delivery_conflict_error import DeliveryConflictError


def _is_recovery_destination(conn: sqlite3.Connection, original_session_id: str, destination_id: str) -> bool:
    """Whether ``destination_id`` is a recovery copy already linked to the original."""
    return (
        conn.execute(
            'SELECT 1 FROM recovery_links WHERE original_session_id = ? AND destination_id = ?',
            (original_session_id, destination_id),
        ).fetchone()
        is not None
    )


def confirm_delivery(
    conn: sqlite3.Connection,
    confirmations: Sequence[DeliveryConfirmation],
    *,
    destination_id: str,
) -> int:
    """Records the delivery of explicitly named claimed inputs.

    The caller owns the transaction and must already have written the transcript
    that carries these messages. Each confirmation is checked against its accepted
    row: the session that accepted it, a claimed state, and the attempt and run that
    carried it. The destination must be that session or a recovery copy already
    linked to it. A mismatch raises before the transaction can commit, so the
    delivery fact, the input state and the transcript land together or not at all.
    """
    if conn is None:
        raise ValueError('Delivery must be confirmed inside the checkpoint transaction')
    if not destination_id:
        raise ValueError('A delivery needs a destination session')
    if conn.execute('SELECT 1 FROM sessions WHERE id = ?', (destination_id,)).fetchone() is None:
        raise DeliveryConflictError('Destination session does not exist')
    seen: set[tuple[str, str]] = set()
    now = datetime.now(UTC).isoformat()
    for confirmation in confirmations:
        key = (confirmation.session_id, confirmation.request_id)
        if key in seen:
            raise DeliveryConflictError('Duplicate delivery confirmation')
        seen.add(key)
        row = conn.execute(
            'SELECT state, attempt_id, library_run_id FROM accepted_inputs WHERE session_id = ? AND request_id = ?',
            key,
        ).fetchone()
        if row is None:
            raise LookupError('Accepted input not found for its session')
        if row['state'] != 'claimed':
            raise DeliveryConflictError('Only a claimed input can be delivered')
        if row['attempt_id'] != confirmation.attempt_id:
            raise DeliveryConflictError('Delivery does not match the recorded attempt')
        if row['library_run_id'] is not None and row['library_run_id'] != confirmation.library_run_id:
            raise DeliveryConflictError('Delivery does not match the recorded run')
        if destination_id != confirmation.session_id and not _is_recovery_destination(
            conn, confirmation.session_id, destination_id
        ):
            raise DeliveryConflictError('Delivery destination is not the accepting session or its recovery copy')
        if (
            conn.execute('SELECT 1 FROM input_deliveries WHERE session_id = ? AND request_id = ?', key).fetchone()
            is not None
        ):
            raise DeliveryConflictError('A delivery fact already exists for this input')
        conn.execute(
            'INSERT INTO input_deliveries '
            '(session_id, request_id, checkpointed, destination_id, destination_session_id) '
            'VALUES (?, ?, 1, ?, ?)',
            (*key, destination_id, destination_id),
        )
        updated = conn.execute(
            "UPDATE accepted_inputs SET state = 'delivered', library_run_id = ?, updated_at = ? "
            "WHERE session_id = ? AND request_id = ? AND state = 'claimed'",
            (confirmation.library_run_id, now, *key),
        ).rowcount
        if updated != 1:
            raise DeliveryConflictError('Delivery lost its claimed input')
    return len(seen)
