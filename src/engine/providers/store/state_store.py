"""Small pieces of state that must survive a restart.

Two things live here:

- ``last_selection``: what the previous session used, so a fresh session opens on
  the model the user was working with instead of an empty one.
- ``installation_id``: a stable ``urn:uuid``. The ChatGPT sign-in needs a host id
  that identifies this installation across sign-ins; generating it per run would
  register a new OAuth client every time.

Everything in this file is optional: if it is lost, the app still works and the
user just has to pick a model again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.engine.providers.domain.selection import Selection
from src.engine.providers.store.atomic import MODE_SHARED, read_json, write_json

VERSION_KEY = 'version'
INSTALLATION_KEY = 'installation_id'
SELECTION_KEY = 'last_selection'
CLIENTS_KEY = 'oauth_clients'
CURRENT_VERSION = 1


def new_installation_id() -> str:
    """A stable, opaque installation identifier in the form OpenAI accepts."""
    return uuid.uuid4().urn


@dataclass(slots=True)
class FileStateStore:
    """Preferences, kept in one small JSON file.

    The store keeps its in-memory copy so a failed write never loses the value
    the running process is using; the error surfaces only as ``save_error``.
    """

    path: Path
    _data: dict[str, object] = field(default_factory=dict, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)
    save_error: str | None = field(default=None, init=False)

    # --- public API --------------------------------------------------------

    def last_selection(self) -> Selection | None:
        raw = self._read().get(SELECTION_KEY)
        return Selection.from_dict(raw) if isinstance(raw, dict) else None

    def set_last_selection(self, selection: Selection | None) -> None:
        data = self._read()
        if selection is None:
            data.pop(SELECTION_KEY, None)
        else:
            data[SELECTION_KEY] = selection.to_dict()
        self._write(data)

    def installation_id(self) -> str:
        """The stable id of this installation, created on first use."""
        data = self._read()
        current = data.get(INSTALLATION_KEY)
        if isinstance(current, str) and current.startswith('urn:uuid:'):
            return current
        created = new_installation_id()
        data[INSTALLATION_KEY] = created
        self._write(data)
        return created

    def stored_client_id(self, alias: str) -> str | None:
        """The OAuth client registered for ``alias`` in a previous sign-in."""
        data = self._read()
        clients = data.get(CLIENTS_KEY)
        if not isinstance(clients, dict):
            return None
        value = clients.get(alias.lower())
        return value if isinstance(value, str) and value else None

    def remember_client_id(self, alias: str, client_id: str) -> None:
        """Records the OAuth client so the next sign-in reuses the registration."""
        data = self._read()
        clients = data.get(CLIENTS_KEY)
        mapping = dict(clients) if isinstance(clients, dict) else {}
        mapping[alias.lower()] = client_id
        data[CLIENTS_KEY] = mapping
        self._write(data)

    def forget(self, account_id: str) -> None:
        """Drops the stored selection when it pointed at ``account_id``."""
        current = self.last_selection()
        if current is not None and current.account_id == account_id:
            self.set_last_selection(None)

    # --- internals ---------------------------------------------------------

    def _read(self) -> dict[str, object]:
        if not self._loaded:
            loaded = read_json(self.path)
            self._data = dict(loaded) if loaded else {}
            self._loaded = True
        return dict(self._data)

    def _write(self, data: dict[str, object]) -> None:
        self._data = dict(data)
        self._loaded = True
        payload = {VERSION_KEY: CURRENT_VERSION, **data}
        try:
            write_json(self.path, payload, mode=MODE_SHARED)
            self.save_error = None
        except OSError as exc:
            # Preferences are a convenience: losing them must not break a turn.
            self.save_error = str(exc)
