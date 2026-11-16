"""The RequestCacheRow value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RequestCacheRow:
    """Usage of a single request, as reported by the provider."""

    index: int
    """Position of the measured request in the session, 1-based."""
    input_tokens: int
    cache_read: int
    cache_write: int
    output_tokens: int
    cost: float | None = None
    cost_estimated: bool = False
    timestamp: datetime | None = None

    @property
    def valid(self) -> bool:
        return (
            0 <= self.cache_read and 0 <= self.cache_write and self.cache_read + self.cache_write <= self.input_tokens
        )

    @property
    def cached(self) -> int:
        """Tokens the provider read from cache."""
        return self.cache_read

    @property
    def uncached(self) -> int | None:
        """Input not reported as either a cache read or a cache write."""
        return self.input_tokens - self.cache_read - self.cache_write if self.valid else None

    @property
    def hit_ratio(self) -> float | None:
        """Fraction of the input read from cache, or None if unavailable."""
        return self.cache_read / self.input_tokens if self.input_tokens and self.valid else None
