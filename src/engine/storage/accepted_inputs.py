"""Durable, per-session input records and their validated state transitions."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from src.engine.attachments.image_attachment import new_image_id
from src.engine.storage.database import Database

OPEN_INPUTS_SQL = "SELECT 1 FROM accepted_inputs WHERE state IN ('accepted', 'claimed', 'delivered') LIMIT 1"
"""The runtime's ``is anything still open`` question, named so its plan can be tested.

The predicate is the whole of it, and it matches ``accepted_inputs_open`` exactly — the
partial index over the three open states, added in schema step 8. That table only
grows and this runs on a timer, so the match is what keeps the poll a lookup instead
of a scan.
"""

InputKind = Literal['turn', 'steer']
InputState = Literal['accepted', 'claimed', 'delivered', 'finished', 'stopped', 'interrupted']
_TRANSITIONS = {
    'accepted': {'claimed', 'stopped', 'interrupted'},
    'claimed': {'stopped', 'interrupted'},
    'delivered': {'finished', 'stopped', 'interrupted'},
    'finished': set(),
    'stopped': set(),
    'interrupted': set(),
}


class InputConflictError(ValueError):
    """A request identifier was reused with different content."""


class DeliveryConflictError(ValueError):
    """A delivery fact does not match the claimed input or its checkpoint."""


@dataclass(frozen=True, slots=True)
class ImageInput:
    data: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class StoredImage:
    data: bytes
    media_type: str
    image_id: str


@dataclass(frozen=True, slots=True)
class AcceptedInput:
    session_id: str
    request_id: str
    sequence: int
    text: str
    images: tuple[StoredImage, ...]
    kind: InputKind
    state: InputState
    attempt_id: str | None
    library_run_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DeliveryConfirmation:
    """The facts that must agree before an input counts as delivered.

    The session and request name the acceptance; the attempt and run are the ones
    that actually carried the message. A missing identifier is not a delivery, and
    passing them explicitly keeps the checkpoint from confirming a whole session by
    coincidence.
    """

    session_id: str
    request_id: str
    attempt_id: str
    library_run_id: str

    def __post_init__(self) -> None:
        if not self.session_id or not self.request_id or not self.attempt_id or not self.library_run_id:
            raise ValueError('A delivery confirmation needs a session, request, attempt and run')


def _canonical(text: str, images: list[ImageInput]) -> str:
    return json.dumps(
        {
            'version': 1,
            'text': text,
            'images': [
                {'data': base64.b64encode(image.data).decode('ascii'), 'media_type': image.media_type}
                for image in images
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    )


def _record(row: sqlite3.Row) -> AcceptedInput:
    payload = json.loads(row['payload'])
    if payload['version'] != 1:
        raise ValueError('Unsupported accepted input payload version')
    images = tuple(
        StoredImage(base64.b64decode(image['data'], validate=True), image['media_type'], image['image_id'])
        for image in payload['images']
    )
    return AcceptedInput(
        row['session_id'],
        row['request_id'],
        row['sequence'],
        payload['text'],
        images,
        row['kind'],
        row['state'],
        row['attempt_id'],
        row['library_run_id'],
        row['created_at'],
        row['updated_at'],
    )


def accept(
    conn: sqlite3.Connection, session_id: str, request_id: str, text: str, images: list[ImageInput], kind: InputKind
) -> AcceptedInput:
    parsed = uuid.UUID(request_id)
    if parsed.version != 4 or parsed.variant != uuid.RFC_4122 or str(parsed) != request_id:
        raise ValueError('request_id must be a canonical UUIDv4')
    if kind not in ('turn', 'steer'):
        raise ValueError('Invalid input kind')
    canonical = _canonical(text, images)
    fingerprint = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    row = conn.execute(
        'SELECT * FROM accepted_inputs WHERE session_id = ? AND request_id = ?', (session_id, request_id)
    ).fetchone()
    if row is not None:
        existing = _record(row)
        stored_images = [ImageInput(image.data, image.media_type) for image in existing.images]
        if row['fingerprint'] != fingerprint or _canonical(existing.text, stored_images) != canonical:
            raise InputConflictError('Request ID was reused with different content')
        return existing
    payload = json.loads(canonical)
    for image in payload['images']:
        image['image_id'] = new_image_id()
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    sequence = conn.execute(
        'SELECT COALESCE(MAX(sequence), 0) + 1 FROM accepted_inputs WHERE session_id = ?', (session_id,)
    ).fetchone()[0]
    now = datetime.now(UTC).isoformat()
    conn.execute(
        'INSERT INTO accepted_inputs (session_id, request_id, sequence, payload, fingerprint, kind, state, created_at, updated_at) '
        "VALUES (?, ?, ?, ?, ?, ?, 'accepted', ?, ?)",
        (session_id, request_id, sequence, encoded, fingerprint, kind, now, now),
    )
    return _record(
        conn.execute(
            'SELECT * FROM accepted_inputs WHERE session_id = ? AND request_id = ?', (session_id, request_id)
        ).fetchone()
    )


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

    def interrupt_open(self) -> int:
        """Database-wide: marks every non-terminal input interrupted, without executing."""
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "UPDATE accepted_inputs SET state = 'interrupted', updated_at = ? "
                "WHERE state IN ('accepted', 'claimed', 'delivered')",
                (now,),
            )
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
