"""Storage: the database, its schema, and the layer that reads and writes rows.

Everything Sigil writes while you work lives here; everything you write while
configuring Sigil stays a file. The line is simple: **if Sigil writes it while you
work, it is data; if you write it while configuring Sigil, it is configuration.**

- `database` — the connection, its pragmas and its transactions.
- `schema` — the DDL, applied whole. There is no import from the old JSON files.
- `blobs` — content-addressed storage: the instructions block and images.
- `history` — a transcript as rows, appended and not rewritten.
- `executions` — the agents of a session, the root included.
"""

from __future__ import annotations

from src.engine.storage.blobs import Blobs
from src.engine.storage.database import Database, database_path
from src.engine.storage.history import history_size, read_history, write_history

__all__ = [
    'Blobs',
    'Database',
    'database_path',
    'history_size',
    'read_history',
    'write_history',
]
