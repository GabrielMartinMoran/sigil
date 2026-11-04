"""Uso del prompt cache por request: cuánto input salió de caché y cuánto no.

`RequestUsage` de pydantic-ai usa buckets inclusivos: `input_tokens` **incluye**
`cache_read_tokens` y `cache_write_tokens` (los adapters normalizan así incluso a
los providers que los reportan separados, como Anthropic). Entonces:

    uncached = input − cache_read − cache_write

Ese es el número caro: los tokens que el provider procesó de cero y que, si el
prefijo del prompt fuera estable, podrían haber salido de caché. Un `uncached`
alto en cada request después del primero es la señal de que algo invalida el
prefijo (instrucciones que se reescriben, historial que se muta, tools que
cambian de orden).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai.messages import ModelMessage, ModelResponse


@dataclass(frozen=True, slots=True)
class RequestCacheRow:
    """Uso de un request individual, tal como lo reportó el provider."""

    index: int
    """Posición del request medido en la sesión, 1-based."""
    input_tokens: int
    cache_read: int
    cache_write: int
    output_tokens: int
    cost: float | None = None

    @property
    def cached(self) -> int:
        """Tokens que el provider sirvió desde caché (leídos o escritos)."""
        return self.cache_read + self.cache_write

    @property
    def uncached(self) -> int:
        """Input procesado de cero: lo que cuesta y no se pudo reusar."""
        return max(0, self.input_tokens - self.cache_read - self.cache_write)

    @property
    def hit_ratio(self) -> float:
        """Fracción del input servida de caché. ``0.0`` sin input."""
        return self.cached / self.input_tokens if self.input_tokens else 0.0


@dataclass(frozen=True, slots=True)
class CacheSummary:
    """Agregado de todos los requests de la sesión."""

    rows: tuple[RequestCacheRow, ...] = field(default_factory=tuple)
    requests: int = 0
    """Requests con usage reportado."""
    unmeasured: int = 0
    """Requests cuyo provider omitió el usage: no suman y se informan aparte."""
    input_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0
    cost: float | None = None
    """Costo reportado, o ``None`` si el provider no lo informó."""

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
        """Fracción del input total que salió de caché."""
        return self.cached / self.input_tokens if self.input_tokens else 0.0


def _cost(response: ModelResponse) -> float | None:
    """Costo en USD del request, si el provider lo informó."""
    cost = getattr(response.usage, 'cost', None)
    return float(cost) if cost is not None else None


def request_cache_rows(messages: Sequence[ModelMessage]) -> list[RequestCacheRow]:
    """Una fila por respuesta medida del modelo, en orden de la sesión.

    Las respuestas sin usage (o con `unmeasured_requests`) no son mediciones y
    no entran: contarlas como cero mentiría sobre el hit ratio.
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
    """Cuántas respuestas del modelo llegaron sin usage utilizable."""
    count = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        usage = message.usage
        if not usage.has_values() or usage.unmeasured_requests:
            count += 1
    return count


def cache_summary(messages: Sequence[ModelMessage]) -> CacheSummary:
    """Agregado del uso de caché de toda la sesión."""
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
