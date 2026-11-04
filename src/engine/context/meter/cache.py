"""Prompt cache usage per request: how much input came from cache and how much did not.

pydantic-ai's `RequestUsage` uses inclusive buckets: `input_tokens` **includes**
`cache_read_tokens` and `cache_write_tokens` (the adapters normalize this way even for
the providers that report them separately, like Anthropic). So:

    uncached = input - cache_read - cache_write

That is the expensive number: the tokens the provider processed from scratch and that, if the
prompt prefix were stable, could have come from cache. A high `uncached`
on every request after the first is the sign that something invalidates the
prefix (instructions being rewritten, history being mutated, tools that
change order).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai.messages import ModelMessage, ModelResponse


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

    @property
    def cached(self) -> int:
        """Tokens the provider served from cache (read or written)."""
        return self.cache_read + self.cache_write

    @property
    def uncached(self) -> int:
        """Input processed from scratch: the expensive part that could not be reused."""
        return max(0, self.input_tokens - self.cache_read - self.cache_write)

    @property
    def hit_ratio(self) -> float:
        """Fraction of the input served from cache. ``0.0`` without input."""
        return self.cached / self.input_tokens if self.input_tokens else 0.0


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
    cost: float | None = None
    """Reported cost, or ``None`` if the provider did not report it."""

    @property
    def has_data(self) -> bool:
        return self.requests > 0

    @property
    def cached(self) -> int:
        return self.cache_read + self.cache_write

    @property
    def uncached(self) -> int:
        return max(0, self.input_tokens - self.cache_read - self.cache_write)

    @property
    def hit_ratio(self) -> float:
        """Fraction of the total input that came from cache."""
        return self.cached / self.input_tokens if self.input_tokens else 0.0


def _cost(response: ModelResponse) -> float | None:
    """Cost of the request in USD, if the provider reported it."""
    cost = getattr(response.usage, 'cost', None)
    return float(cost) if cost is not None else None


def request_cache_rows(messages: Sequence[ModelMessage]) -> list[RequestCacheRow]:
    """One row per measured model response, in session order.

    Responses without usage (or with `unmeasured_requests`) are not measurements and
    do not get in: counting them as zero would lie about the hit ratio.
    """
    rows: list[RequestCacheRow] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        usage = message.usage
        if not usage.has_values() or usage.unmeasured_requests:
            continue
        rows.append(
            RequestCacheRow(
                index=len(rows) + 1,
                input_tokens=usage.input_tokens or 0,
                cache_read=usage.cache_read_tokens or 0,
                cache_write=usage.cache_write_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cost=_cost(message),
            )
        )
    return rows


def unmeasured_responses(messages: Sequence[ModelMessage]) -> int:
    """How many model responses arrived without usable usage."""
    count = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        usage = message.usage
        if not usage.has_values() or usage.unmeasured_requests:
            count += 1
    return count


def cache_summary(messages: Sequence[ModelMessage]) -> CacheSummary:
    """Aggregate of the cache usage of the whole session."""
    rows = request_cache_rows(messages)
    if not rows:
        return CacheSummary(unmeasured=unmeasured_responses(messages))
    known = [row.cost for row in rows if row.cost is not None]
    return CacheSummary(
        rows=tuple(rows),
        requests=len(rows),
        unmeasured=unmeasured_responses(messages),
        input_tokens=sum(row.input_tokens for row in rows),
        cache_read=sum(row.cache_read for row in rows),
        cache_write=sum(row.cache_write for row in rows),
        output_tokens=sum(row.output_tokens for row in rows),
        cost=sum(known) if known else None,
    )
