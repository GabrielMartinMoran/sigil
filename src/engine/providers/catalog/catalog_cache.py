"""In-memory TTL cache for catalogues.

A catalogue changes rarely and a picker is opened repeatedly; refetching a
400-entry list on every keystroke would be absurd. The cache is per process and
per account, and it is invalidated explicitly when an account is removed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.engine.providers.domain.model_capability import ModelCatalog

DEFAULT_TTL = 600.0
"""Seconds a fetched catalogue stays fresh. Ten minutes."""


@dataclass(slots=True)
class _Entry:
    catalog: ModelCatalog
    fetched_at: float


@dataclass(slots=True)
class CatalogCache:
    """Freshness bookkeeping for the fetched catalogues."""

    ttl: float = DEFAULT_TTL
    _entries: dict[str, _Entry] = field(default_factory=dict, init=False, repr=False)
    _clock: object = field(default=time.monotonic, init=False, repr=False)

    def get(self, account_id: str) -> ModelCatalog | None:
        """The cached catalogue, or ``None`` when missing or stale."""
        entry = self._entries.get(account_id)
        if entry is None:
            return None
        if self._clock() - entry.fetched_at >= self.ttl:
            self._entries.pop(account_id, None)
            return None
        return entry.catalog

    def put(self, account_id: str, catalog: ModelCatalog) -> None:
        self._entries[account_id] = _Entry(catalog=catalog, fetched_at=self._clock())

    def invalidate(self, account_id: str | None = None) -> None:
        """Drops one account's entry, or the whole cache when no id is given."""
        if account_id is None:
            self._entries.clear()
        else:
            self._entries.pop(account_id, None)
