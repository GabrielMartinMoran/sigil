"""Preferences that must survive a restart: the last model and the sign-in ids.

Rows in `app_state`, keyed by name, with JSON values. The store keeps the interface
the rest of the provider layer already uses so nothing above it has to change.

Everything here is optional: if it is lost, the app still works and the user just has
to pick a model again. That is why a failed write surfaces as `save_error` and never
as an exception.
"""

from __future__ import annotations

from typing import Protocol

from src.core.providers.domain.selection import Selection


class StateStore(Protocol):
    """The preferences the provider layer reads and writes."""

    save_error: str | None

    def last_selection(self) -> Selection | None: ...

    def set_last_selection(self, selection: Selection | None) -> None: ...

    def model_windows(self, account_id: str) -> dict[str, int]: ...

    def remember_windows(self, account_id: str, windows: dict[str, int]) -> None: ...

    def installation_id(self) -> str: ...

    def stored_client_id(self, alias: str) -> str | None: ...

    def remember_client_id(self, alias: str, client_id: str) -> None: ...

    def forget(self, account_id: str) -> None: ...


__all__ = ['StateStore']
