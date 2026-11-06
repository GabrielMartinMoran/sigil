"""Preferences that must survive a restart: the last model and the sign-in ids.

Rows in `app_state`, keyed by name, with JSON values. The store keeps the interface
the rest of the provider layer already uses so nothing above it has to change.

Everything here is optional: if it is lost, the app still works and the user just has
to pick a model again. That is why a failed write surfaces as `save_error` and never
as an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from src.engine.providers.domain.selection import Selection
from src.engine.storage.app_state import CLIENTS_KEY, SELECTION_KEY, AppState, new_installation_id
from src.engine.storage.database import Database, database_path


class StateStore(Protocol):
    """The preferences the provider layer reads and writes."""

    save_error: str | None

    def last_selection(self) -> Selection | None: ...

    def set_last_selection(self, selection: Selection | None) -> None: ...

    def installation_id(self) -> str: ...

    def stored_client_id(self, alias: str) -> str | None: ...

    def remember_client_id(self, alias: str, client_id: str) -> None: ...

    def forget(self, account_id: str) -> None: ...


@dataclass(slots=True)
class SqliteStateStore:
    """Preferences on the database, with the same shape as the file version."""

    base_dir: Path | None = None
    _state: AppState = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._state = AppState(Database.at(database_path(self.base_dir)))

    @property
    def save_error(self) -> str | None:
        return self._state.save_error

    # --- public API --------------------------------------------------------

    def last_selection(self) -> Selection | None:
        raw = self._state.get(SELECTION_KEY)
        return Selection.from_dict(raw) if isinstance(raw, dict) else None

    def set_last_selection(self, selection: Selection | None) -> None:
        self._state.put(SELECTION_KEY, None if selection is None else selection.to_dict())

    def installation_id(self) -> str:
        """The stable id of this installation, created on first use."""
        return self._state.installation_id()

    def stored_client_id(self, alias: str) -> str | None:
        """The OAuth client registered for ``alias`` in a previous sign-in."""
        clients = self._state.get(CLIENTS_KEY)
        if not isinstance(clients, dict):
            return None
        value = clients.get(alias.lower())
        return value if isinstance(value, str) and value else None

    def remember_client_id(self, alias: str, client_id: str) -> None:
        """Records the OAuth client so the next sign-in reuses the registration."""
        clients = self._state.get(CLIENTS_KEY)
        mapping = dict(clients) if isinstance(clients, dict) else {}
        mapping[alias.lower()] = client_id
        self._state.put(CLIENTS_KEY, mapping)

    def forget(self, account_id: str) -> None:
        """Drops the stored selection when it pointed at ``account_id``."""
        current = self.last_selection()
        if current is not None and current.account_id == account_id:
            self.set_last_selection(None)


__all__ = ['SqliteStateStore', 'StateStore', 'new_installation_id']
