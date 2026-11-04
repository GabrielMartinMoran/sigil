"""Reintentos ante fallos transitorios del proveedor del modelo.

Un 502 del upstream, un 429 del proxy o una conexión cortada tumbaban el turno
entero: el error llegaba a la UI y el flujo se frenaba. Esta capability usa el
hook ``on_model_request_error`` de pydantic-ai para volver a pedir la respuesta
mientras el error sea transitorio y queden intentos.

El reintento no toca el historial ni le avisa al modelo de que hubo un intento
fallido: el framework se queda en el mismo paso y sólo repite la llamada, así
que las tools ya ejecutadas no se vuelven a ejecutar.

La app corre con ``event_stream_handler``, así que pydantic-ai streamea por
dentro y un fallo puede llegar con ``in_stream=True``. Se reintenta igual: si el
stream murió antes de emitir texto no se ve nada, y si murió a mitad el usuario
ya vio una respuesta parcial, pero el aviso de reintento en el chat explica por
qué el turno sigue.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING

import anyio
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import (
    ModelConnectionError,
    ModelHTTPError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelRequestRejectedError,
    ModelServerError,
    ModelUnavailableError,
    RetryModelRequest,
)
from pydantic_ai.tools import AgentDepsT, RunContext

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models import ModelRequestContext

DEFAULT_MAX_ATTEMPTS = 5
"""Intentos totales por request, contando el primero."""

BASE_DELAY = 0.5
"""Espera base del backoff exponencial, en segundos."""

MAX_DELAY = 8.0
"""Tope de la espera entre intentos, en segundos."""

RETRYABLE_ERRORS = (
    ModelRateLimitError,  # 429
    ModelUnavailableError,  # 503 y su subclase 529 (overloaded)
    ModelServerError,  # 500, 502, 504
    ModelConnectionError,  # conexión cortada y timeouts de transporte
)
"""Categorías de pydantic-ai que valen otro intento.

Quedan afuera las que no mejoran esperando: cuota o facturación agotada,
request rechazado (400/401/404/422) y ventana de contexto excedida.
"""

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})
"""Status HTTP que valen otro intento cuando el error no viene categorizado."""

NON_RETRYABLE_ERRORS = (
    ModelQuotaExceededError,  # 402 y los 429 de cuota/facturación
    ModelRequestRejectedError,  # 400/422 y sus subclases (auth, 404, contexto)
)
"""Categorías que nunca mejoran esperando, aunque el status parezca reintentable.

Un 429 de cuota agotada comparte status con un 429 de rate limit, pero el primero
no se resuelve solo: sin este filtro se gastarían los cinco intentos al vacío.
"""


def is_transient(error: BaseException) -> bool:
    """Si ``error`` es de los que pueden resolverse reintentando."""
    # El descarte por categoría va primero: un error permanente con status 429
    # no debe colarse por el fallback de abajo.
    if isinstance(error, NON_RETRYABLE_ERRORS):
        return False
    if isinstance(error, RETRYABLE_ERRORS):
        return True
    if isinstance(error, ModelHTTPError):
        # Adaptadores o modelos propios que no clasifican: el status alcanza.
        return error.status_code in RETRYABLE_STATUS_CODES
    return False


def describe_error(error: BaseException) -> str:
    """Resumen de una línea del error, apto para mostrar en el chat."""
    status = getattr(error, 'status_code', None)
    detail = _one_line(getattr(error, 'body', None)) or _one_line(str(error)) or type(error).__name__
    if status is None:
        return f'{type(error).__name__} · {detail}'
    return f'HTTP {status} · {detail}'


def _one_line(value: object, limit: int = 140) -> str:
    if value is None:
        return ''
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


@dataclass(frozen=True)
class RetryNotice:
    """Dato que se entrega al notificador cuando se va a reintentar."""

    attempt: int
    """Número del intento que acaba de fallar (arranca en 1)."""
    next_attempt: int
    max_attempts: int
    delay: float
    """Segundos de espera antes del próximo intento."""
    reason: str


@dataclass
class RetryTransientModelErrors(AbstractCapability[AgentDepsT]):
    """Reintenta la llamada al modelo ante fallos transitorios del proveedor."""

    _: KW_ONLY

    id: str | None = 'retry_transient_model_errors'
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay: float = BASE_DELAY
    max_delay: float = MAX_DELAY
    on_retry: Callable[[RetryNotice], None] | None = None
    """Callback opcional (la UI lo usa para avisar del reintento)."""

    async def on_model_request_error(
        self,
        ctx: RunContext[AgentDepsT],
        *,
        request_context: ModelRequestContext,
        error: Exception,
    ) -> ModelResponse:
        attempt = request_context.attempt
        if attempt >= self.max_attempts or not is_transient(error):
            raise error

        delay = self.delay_for(attempt, getattr(error, 'retry_after', None))
        self._notify(
            RetryNotice(
                attempt=attempt,
                next_attempt=attempt + 1,
                max_attempts=self.max_attempts,
                delay=delay,
                reason=describe_error(error),
            )
        )
        if delay > 0:
            await anyio.sleep(delay)
        raise RetryModelRequest()

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Espera antes del intento siguiente.

        Si el proveedor pidió un ``Retry-After`` se respeta (con el tope de
        ``max_delay``); si no, backoff exponencial con jitter para que varios
        turnos no reintenten sincronizados.
        """
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        backoff = self.base_delay * 2 ** (attempt - 1)
        return min(backoff * random.uniform(0.5, 1.5), self.max_delay)

    def _notify(self, notice: RetryNotice) -> None:
        if self.on_retry is None:
            return
        try:
            self.on_retry(notice)
        except Exception:  # noqa: BLE001, S110 - avisar nunca debe romper el reintento
            pass


__all__ = [
    'DEFAULT_MAX_ATTEMPTS',
    'RetryNotice',
    'RetryTransientModelErrors',
    'describe_error',
    'is_transient',
]
