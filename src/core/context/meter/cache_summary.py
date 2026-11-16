"""The CacheSummary value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.context.meter.request_cache_row import RequestCacheRow


@dataclass(frozen=True, slots=True)
class CacheSummary:
    """Aggregate of every request in the session."""

    rows: tuple[RequestCacheRow, ...] = field(default_factory=tuple)
    requests: int = 0
    """Requests with reported usage."""
    unmeasured: int = 0
    """Requests whose provider omitted the usage: they do not add up and are reported separately."""
    input_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0
    cost_requests: int = 0
    estimated_cost_requests: int = 0
    cost: float | None = None
    """Sum of available reported or estimated costs, or ``None`` if unavailable."""

    @property
    def has_data(self) -> bool:
        return self.requests > 0

    @property
    def valid(self) -> bool:
        return (
            all(row.valid for row in self.rows)
            and 0 <= self.cache_read
            and 0 <= self.cache_write
            and self.cache_read + self.cache_write <= self.input_tokens
        )

    @property
    def cached(self) -> int:
        return self.cache_read

    @property
    def uncached(self) -> int | None:
        return self.input_tokens - self.cache_read - self.cache_write if self.valid else None

    @property
    def hit_ratio(self) -> float | None:
        """Fraction of the total input that came from cache."""
        return self.cache_read / self.input_tokens if self.input_tokens and self.valid else None
