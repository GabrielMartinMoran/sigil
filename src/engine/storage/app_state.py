"""Small pieces of state that must survive a restart, as rows.

Two things live here and they are not secrets:

- ``last_selection``: what the previous session used, so a fresh session opens on
  the model the user was working with instead of an empty one.
- ``installation_id``: a stable ``urn:uuid``. The ChatGPT sign-in needs a host id
  that identifies this installation across sign-ins; generating it per run would
  register a new OAuth client every time.

Everything here is optional: if it is lost, the app still works and the user just
has to pick a model again. That is why a failed write is reported and not raised.

Credentials do **not** live here. They are a file with ``0600`` because secrets do
not share permissions with data that must be world-readable, and a keyring is the
next step.
"""

from __future__ import annotations

import json
import uuid

from src.engine.storage.database import Database

INSTALLATION_KEY = 'installation_id'
SELECTION_KEY = 'last_selection'
CLIENTS_KEY = 'oauth_clients'


def new_installation_id() -> str:
    """A stable, opaque installation identifier in the form OpenAI accepts."""
    return uuid.uuid4().urn


class AppState:
    """A key/value store with JSON values, on the database."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self.save_error: str | None = None

    # --- reading -------------------------------------------------------------

    def get(self, key: str) -> object | None:
        row = self._db.one('SELECT value FROM app_state WHERE key = ?', (key,))
        if row is None:
            return None
        try:
            return json.loads(row['value'])
        except ValueError:
            return None

    def put(self, key: str, value: object | None) -> None:
        """Writes a value, or removes the key when it is ``None``."""
        try:
            if value is None:
                self._db.execute('DELETE FROM app_state WHERE key = ?', (key,))
            else:
                self._db.execute(
                    'INSERT INTO app_state (key, value) VALUES (?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                    (key, json.dumps(value, ensure_ascii=False, separators=(',', ':'))),
                )
            self.save_error = None
        except Exception as exc:  # noqa: BLE001 - preferences never break a turn
            self.save_error = str(exc)

    # --- the two values the app asks for -----------------------------------

    def installation_id(self) -> str:
        """The stable id of this installation, created on first use."""
        current = self.get(INSTALLATION_KEY)
        if isinstance(current, str) and current.startswith('urn:uuid:'):
            return current
        created = new_installation_id()
        self.put(INSTALLATION_KEY, created)
        return created


__all__ = ['CLIENTS_KEY', 'INSTALLATION_KEY', 'SELECTION_KEY', 'AppState', 'new_installation_id']
