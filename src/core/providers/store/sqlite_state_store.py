"""The SqliteStateStore value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.core.providers.domain.selection import Selection
from src.core.storage.app_state import CLIENTS_KEY, SELECTION_KEY, AppState
from src.core.storage.database import Database
from src.core.storage.database_path import database_path


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

    def model_windows(self, account_id: str) -> dict[str, int]:
        raw = self._state.get(f'model_windows:{account_id}')
        if not isinstance(raw, dict):
            return {}
        return {
            name: value for name, value in raw.items() if isinstance(name, str) and type(value) is int and value > 0
        }

    def remember_windows(self, account_id: str, windows: dict[str, int]) -> None:
        valid = {name: value for name, value in windows.items() if type(value) is int and value > 0}
        if valid:
            self._state.put(f'model_windows:{account_id}', {**self.model_windows(account_id), **valid})

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
