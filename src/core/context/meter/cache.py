"""Provider-reported prompt usage, keeping cache reads separate from writes.

Input tokens include reads and writes. A write is processing for future reuse,
not a cache hit. Zero may also mean a provider omitted cache details.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic_ai.messages import ModelMessage, ModelResponse

from src.core.context.meter.cache_summary import CacheSummary
from src.core.context.meter.cost import request_amount
from src.core.context.meter.request_cache_row import RequestCacheRow


def format_call_time(timestamp: datetime | None) -> str:
    """Format a response timestamp in the local time zone."""
    return timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S') if timestamp else '?'


def format_call_cost(amount: Decimal | float | None, estimated: bool = False) -> str:
    """Distinguish estimated charges from reported charges and unknown values."""
    return f'{"~" if estimated else ""}${amount}' if amount is not None else '?'


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
                timestamp=message.timestamp,
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
