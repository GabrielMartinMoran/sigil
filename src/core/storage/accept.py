"""The accept function of the storage layer."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from src.core.attachments.image_metadata import new_image_id
from src.core.storage.accepted_input import AcceptedInput, InputKind
from src.core.storage.image_input import ImageInput
from src.core.storage.input_conflict_error import InputConflictError
from src.core.storage.stored_image import StoredImage


def _canonical(text: str, images: list[ImageInput], origin: dict[str, Any] | None = None) -> str:
    return json.dumps(
        {
            **({'origin': origin} if origin else {}),
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
        payload.get('origin'),
    )


def accept(
    conn: sqlite3.Connection,
    session_id: str,
    request_id: str,
    text: str,
    images: list[ImageInput],
    kind: InputKind,
    origin: dict[str, Any] | None = None,
) -> AcceptedInput:
    parsed = uuid.UUID(request_id)
    if parsed.version != 4 or parsed.variant != uuid.RFC_4122 or str(parsed) != request_id:
        raise ValueError('request_id must be a canonical UUIDv4')
    if kind not in ('turn', 'steer'):
        raise ValueError('Invalid input kind')
    canonical = _canonical(text, images, origin)
    fingerprint = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    row = conn.execute(
        'SELECT * FROM accepted_inputs WHERE session_id = ? AND request_id = ?', (session_id, request_id)
    ).fetchone()
    if row is not None:
        existing = _record(row)
        stored_images = [ImageInput(image.data, image.media_type) for image in existing.images]
        if row['fingerprint'] != fingerprint or _canonical(existing.text, stored_images, existing.origin) != canonical:
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
