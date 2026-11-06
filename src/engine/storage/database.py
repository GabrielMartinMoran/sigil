"""The connection to Sigil's database, and the pragmas it needs.

One database holds every workspace: a session belongs to a directory, and `/load`
lists the sessions of every worktree of the repository, which is a join when the
rows live together and a loop over connections when they do not.

Two pragmas are load-bearing and one of them defaults to the wrong value:

- ``journal_mode = WAL`` is what makes an append cost a page in the log instead of
  a rewrite of the file. It is stored in the database itself.
- ``foreign_keys = ON`` is **per connection and off by default**. Every cascade in
  the schema — a session taking its executions, an execution its turns, a turn its
  messages and the subagents it summoned — is inert without it, and a connection
  that forgets it does not fail: it silently leaves orphans behind.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.engine.storage.schema import SCHEMA, USER_VERSION

DEFAULT_DATABASE_NAME = 'sigil.db'

_PRAGMAS = (
    'PRAGMA journal_mode = WAL',
    'PRAGMA synchronous = NORMAL',
    'PRAGMA foreign_keys = ON',
    'PRAGMA busy_timeout = 5000',
    'PRAGMA temp_store = MEMORY',
)

_instances: OrderedDict[str, Database] = OrderedDict()
_instances_lock = threading.Lock()
MAX_INSTANCES = 8
"""How many databases stay open.

One is the app. Eight leaves room for a test that works on a few at once, and evicting
the oldest is what keeps a long run from exhausting the process's descriptors.
"""


class Database:
    """One connection to one database file, with the schema applied.

    The connection is shared by every caller of the same path: the store, the
    roadmap and the provider accounts all reach the same file, and opening a
    connection per object would multiply the WAL readers for no reason. SQLite
    serializes writers itself; the lock here is only for the Python side of
    ``sqlite3``, which is not thread-safe on a shared connection.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            isolation_level=None,  # transactions are explicit, see `transaction`
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        for pragma in _PRAGMAS:
            self._connection.execute(pragma)
        self._apply_schema()

    # --- schema --------------------------------------------------------------

    def _apply_schema(self) -> None:
        with self._lock:
            current = self._connection.execute('PRAGMA user_version').fetchone()[0]
            if current == USER_VERSION:
                return
            self._connection.executescript(SCHEMA)
            self._connection.execute(f'PRAGMA user_version = {USER_VERSION}')

    # --- access --------------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def execute(self, sql: str, parameters: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._connection.execute(sql, parameters)

    def query(self, sql: str, parameters: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchall()

    def one(self, sql: str, parameters: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchone()

    def value(self, sql: str, parameters: tuple | dict = (), default: object = None) -> object:
        row = self.one(sql, parameters)
        if row is None or row[0] is None:
            return default
        return row[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One all-or-nothing unit of work.

        ``IMMEDIATE`` takes the write lock up front: two writers then queue on
        ``busy_timeout`` instead of one of them discovering the conflict at commit
        time, halfway through its statements.
        """
        with self._lock:
            self._connection.execute('BEGIN IMMEDIATE')
            try:
                yield self._connection
            except BaseException:
                self._connection.execute('ROLLBACK')
                raise
            else:
                self._connection.execute('COMMIT')

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # --- lifecycle -----------------------------------------------------------

    @classmethod
    def at(cls, path: str | Path) -> Database:
        """The shared instance for ``path``, opened on first use.

        The cache is bounded: the least recently used connection is closed when a new
        one arrives. It is safe because callers ask for the instance again instead of
        holding one across calls — the store, the roadmap and the provider accounts
        each resolve it from their own path when they need it.
        """
        key = str(Path(path).expanduser().resolve())
        with _instances_lock:
            existing = _instances.get(key)
            if existing is not None:
                _instances.move_to_end(key)
                return existing
            created = cls(key)
            _instances[key] = created
            while len(_instances) > MAX_INSTANCES:
                _oldest, evicted = _instances.popitem(last=False)
                evicted.close()
            return created

    @classmethod
    def forget(cls, path: str | Path) -> None:
        """Drops the shared instance, closing it. Used by tests."""
        key = str(Path(path).expanduser().resolve())
        with _instances_lock:
            existing = _instances.pop(key, None)
        if existing is not None:
            existing.close()


def database_path(base_dir: str | Path | None = None) -> Path:
    """``<base>/sigil.db``, with the base resolved by the caller."""
    from src.engine.paths import sigil_home

    base = Path(base_dir) if base_dir is not None else sigil_home()
    return base / DEFAULT_DATABASE_NAME


__all__ = ['DEFAULT_DATABASE_NAME', 'MAX_INSTANCES', 'Database', 'database_path']
