"""Accounts, as rows.

It holds no secrets: only the identity of the account and how to use it. Two things
the file version checked in code are constraints now — the id is the primary key,
and the alias is unique ignoring case — so a duplicate cannot be written even by a
second process.

An account whose row cannot be rebuilt is skipped, with the rest still readable,
because a single bad value should never make the list unloadable.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import AliasError
from src.engine.storage.database import Database, database_path


class SqliteAccountStore:
    """The list of configured accounts."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._db = Database.at(database_path(base_dir))

    @property
    def database(self) -> Database:
        return self._db

    def load(self) -> tuple[ProviderAccount, ...]:
        """Every valid account, in the order they were created."""
        rows = self._db.query(
            'SELECT id, alias, kind, variant, auth, created_at, last_model FROM accounts ORDER BY created_at, alias'
        )
        accounts: list[ProviderAccount] = []
        for row in rows:
            entry = {
                'id': row['id'],
                'alias': row['alias'],
                'kind': row['kind'],
                'variant': row['variant'],
                'auth': row['auth'],
                'created_at': row['created_at'],
                'last_model': row['last_model'],
            }
            try:
                accounts.append(ProviderAccount.from_dict(entry))
            except KeyError, TypeError, ValueError:
                # A row this build cannot read is one account lost, not the list.
                continue
        return tuple(accounts)

    def save(self, accounts: Iterable[ProviderAccount]) -> None:
        """Makes the stored list equal to ``accounts``, in one transaction."""
        wanted = list(accounts)
        keep = [account.id for account in wanted]
        with self._db.transaction() as conn:
            if keep:
                placeholders = ','.join('?' for _ in keep)
                conn.execute(f'DELETE FROM accounts WHERE id NOT IN ({placeholders})', keep)
            else:
                conn.execute('DELETE FROM accounts')
            for account in wanted:
                conn.execute(
                    'INSERT INTO accounts (id, alias, alias_key, kind, variant, auth, created_at, last_model) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
                    'ON CONFLICT(id) DO UPDATE SET '
                    'alias = excluded.alias, alias_key = excluded.alias_key, kind = excluded.kind, '
                    'variant = excluded.variant, auth = excluded.auth, last_model = excluded.last_model',
                    (
                        account.id,
                        account.alias,
                        account.matches_alias,
                        str(account.kind),
                        str(account.variant),
                        str(account.auth),
                        account.created_at,
                        account.last_model,
                    ),
                )

    def find_alias(self, alias: str) -> ProviderAccount | None:
        """Case-insensitive lookup by alias."""
        row = self._db.one('SELECT id FROM accounts WHERE alias_key = ?', (alias.strip().lower(),))
        return None if row is None else self.find_id(str(row['id']))

    def find_id(self, account_id: str) -> ProviderAccount | None:
        for account in self.load():
            if account.id == account_id:
                return account
        return None

    def ensure_free_alias(self, alias: str, *, ignore_id: str | None = None) -> None:
        """Raises ``AliasError`` when ``alias`` already names another account.

        The database enforces this too, with a unique `alias_key`; checking first is
        what turns a constraint violation into a sentence the user can act on.
        """
        wanted = alias.strip().lower()
        for account in self.load():
            if account.matches_alias == wanted and account.id != ignore_id:
                raise AliasError(f"There is already an account called '{account.alias}'.")


__all__ = ['SqliteAccountStore']
