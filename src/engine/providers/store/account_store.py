"""Accounts on disk.

One JSON array. It holds no secrets: only the identity of the account and how to
use it. An account whose entry is corrupt is skipped with the rest of the file
still readable, because a single bad hand-edit should never wipe the list.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import AliasError
from src.engine.providers.store.atomic import MODE_SHARED, read_json, write_json

ACCOUNTS_KEY = 'accounts'
VERSION_KEY = 'version'
CURRENT_VERSION = 1


class FileAccountStore:
    """The list of configured accounts."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[ProviderAccount, ...]:
        """Every valid account, in file order."""
        data = read_json(self._path)
        if not data:
            return ()
        entries = data.get(ACCOUNTS_KEY)
        if not isinstance(entries, list):
            return ()
        accounts: list[ProviderAccount] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                accounts.append(ProviderAccount.from_dict(entry))
            except KeyError, TypeError, ValueError:
                continue
        return tuple(accounts)

    def save(self, accounts: Iterable[ProviderAccount]) -> None:
        payload = {
            VERSION_KEY: CURRENT_VERSION,
            ACCOUNTS_KEY: [account.to_dict() for account in accounts],
        }
        write_json(self._path, payload, mode=MODE_SHARED)

    def find_alias(self, alias: str) -> ProviderAccount | None:
        """Case-insensitive lookup by alias."""
        wanted = alias.strip().lower()
        for account in self.load():
            if account.matches_alias == wanted:
                return account
        return None

    def find_id(self, account_id: str) -> ProviderAccount | None:
        for account in self.load():
            if account.id == account_id:
                return account
        return None

    def ensure_free_alias(self, alias: str, *, ignore_id: str | None = None) -> None:
        """Raises ``AliasError`` when ``alias`` already names another account."""
        wanted = alias.strip().lower()
        for account in self.load():
            if account.matches_alias == wanted and account.id != ignore_id:
                raise AliasError(f"There is already an account called '{account.alias}'.")
