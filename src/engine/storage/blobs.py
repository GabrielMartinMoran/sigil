"""Content-addressed storage for the things written more than once.

The 327.9 MB that an install of fifty sessions used to occupy were, in 85% of
their bytes, the same 23 KB instruction block written again on every request. In
the whole install there were 64 distinct blocks, totalling 3.12 MB. Content
addressing is what turns that into 64 rows and 1,400 integers.
"""

from __future__ import annotations

import hashlib
import sqlite3

from src.engine.storage.database import Database

INSTRUCTIONS = 'instructions'
IMAGE = 'image'
ROADMAP = 'roadmap'
ABANDONED = 'abandoned'
"""The four kinds. `roadmap` and `abandoned` belong to rollback and are referenced
from `checkpoints`; the collector must treat those columns as references or a
rollback loses the plan it restores and the tail it preserves."""


class Blobs:
    """Read and write blobs by content, inside one database."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._text_cache: dict[int, str] = {}

    # --- writing -------------------------------------------------------------

    def put(
        self,
        data: bytes | str,
        *,
        kind: str,
        media_type: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Stores ``data`` and returns its id. Two equal payloads are one row."""
        raw = data.encode('utf-8') if isinstance(data, str) else data
        digest = hashlib.sha256(raw).hexdigest()
        execute = (conn or self._db.connection).execute
        row = execute('SELECT id FROM blobs WHERE sha256 = ?', (digest,)).fetchone()
        if row is not None:
            return int(row[0])
        cursor = execute(
            'INSERT INTO blobs (sha256, kind, media_type, size, data) VALUES (?, ?, ?, ?, ?)',
            (digest, kind, media_type, len(raw), raw),
        )
        return int(cursor.lastrowid or 0)

    def put_text(self, text: str, *, kind: str = INSTRUCTIONS, conn: sqlite3.Connection | None = None) -> int:
        return self.put(text, kind=kind, media_type='text/plain; charset=utf-8', conn=conn)

    # --- reading -------------------------------------------------------------

    def text(self, blob_id: int) -> str:
        """The text of a blob, cached: there are only a handful of distinct ones."""
        cached = self._text_cache.get(blob_id)
        if cached is not None:
            return cached
        row = self._db.one('SELECT data FROM blobs WHERE id = ?', (blob_id,))
        if row is None:
            return ''
        value = bytes(row[0]).decode('utf-8')
        if len(self._text_cache) > 256:
            self._text_cache.clear()
        self._text_cache[blob_id] = value
        return value

    def data(self, blob_id: int) -> bytes:
        row = self._db.one('SELECT data FROM blobs WHERE id = ?', (blob_id,))
        return b'' if row is None else bytes(row[0])

    def forget_cache(self) -> None:
        self._text_cache.clear()

    # --- maintenance ---------------------------------------------------------

    def collect_unreferenced(self) -> int:
        """Deletes instruction blobs nothing points at any more.

        Only ``instructions``: an ``image`` blob is referenced from inside a
        message payload, not from a column, so the collector cannot see that
        reference and would delete pixels `view_image` still needs. Images are
        collected by age and by session instead.
        """
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM blobs WHERE kind = 'instructions' "
                'AND id NOT IN (SELECT instructions_id FROM messages WHERE instructions_id IS NOT NULL)'
            )
            removed = cursor.rowcount or 0
        self.forget_cache()
        return removed


__all__ = ['ABANDONED', 'IMAGE', 'INSTRUCTIONS', 'ROADMAP', 'Blobs']
