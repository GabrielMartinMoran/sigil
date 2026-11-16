"""A composition's active selection over shared installation preferences."""

from __future__ import annotations

from src.core.providers.domain.selection import Selection
from src.core.providers.store.state_store import StateStore


class SessionStateStore:
    def __init__(self, shared: StateStore) -> None:
        self.shared = shared.shared if isinstance(shared, SessionStateStore) else shared
        self._selection = self.shared.last_selection()

    @property
    def save_error(self) -> str | None:
        return self.shared.save_error

    def last_selection(self) -> Selection | None:
        return self._selection

    def set_last_selection(self, selection: Selection | None) -> None:
        self._selection = selection
        self.shared.set_last_selection(selection)

    def model_windows(self, account_id: str) -> dict[str, int]:
        return self.shared.model_windows(account_id)

    def remember_windows(self, account_id: str, windows: dict[str, int]) -> None:
        self.shared.remember_windows(account_id, windows)

    def installation_id(self) -> str:
        return self.shared.installation_id()

    def stored_client_id(self, alias: str) -> str | None:
        return self.shared.stored_client_id(alias)

    def remember_client_id(self, alias: str, client_id: str) -> None:
        self.shared.remember_client_id(alias, client_id)

    def forget(self, account_id: str) -> None:
        if self._selection is not None and self._selection.account_id == account_id:
            self._selection = None
        self.shared.forget(account_id)
