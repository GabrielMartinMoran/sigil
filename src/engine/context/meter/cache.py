"""Provider-reported prompt usage, keeping cache reads separate from writes.

Input tokens include reads and writes. A write is processing for future reuse,
not a cache hit. Zero may also mean a provider omitted cache details.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai.messages import ModelMessage, ModelResponse

from src.engine.context.meter.cost import request_amount


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


def _cost(response: ModelResponse) -> tuple[float | None, bool]:
    """Available request cost and whether it is estimated."""
    cost, reported = request_amount(response)
    return (float(cost), not reported) if cost is not None else (None, False)


def request_cache_rows(messages: Sequence[ModelMessage]) -> list[RequestCacheRow]:
    """One row per measured model response, in session order.

    Responses without usage (or with `unmeasured_requests`) are not measurements and
    do not get in: counting them as zero would lie about the hit ratio.
    """
    rows: list[RequestCacheRow] = []
    index = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        index += 1
        usage = message.usage
        if not usage.has_values() or usage.unmeasured_requests:
            continue
        cost, estimated = _cost(message)
        rows.append(
            RequestCacheRow(
                index=index,
                input_tokens=usage.input_tokens or 0,
                cache_read=usage.cache_read_tokens or 0,
                cache_write=usage.cache_write_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cost=cost,
                cost_estimated=estimated,
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
        cost_requests=len(known),
        estimated_cost_requests=sum(row.cost_estimated for row in rows),
    )
