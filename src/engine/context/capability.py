"""Capability que aplica la estrategia de contexto a cada request.

Es deliberadamente **no destructiva**: toca sólo `request_context.messages`, que
es una lista propia del request, y deja `ctx.messages` intacto. Así
`run.all_messages()` sigue devolviendo el log completo y la sesión no pierde nada.

`ProcessHistory` de pydantic-ai no sirve para esto: hace `ctx.messages[:] = ...`
y reescribe el historial persistente.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.request import RequestSnapshot, snapshot_request
from src.engine.context.overhead import measure_request_parameters
from src.engine.context.strategy.protocol import ContextStrategy
from src.engine.context.strategy.view_result import ViewResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.tools import RunContext


@dataclass
class ContextViewCapability(AbstractCapability[Any]):
    """Proyecta el historial a la vista del modelo antes de cada request."""

    _: KW_ONLY

    strategy: ContextStrategy
    meter: ContextMeter
    """Se actualiza con el costo real de instrucciones y tools de cada request."""

    on_view: Callable[[ViewResult], None] | None = None
    """Callback de diagnóstico. No debe romper el request si falla."""

    id: str | None = 'context_view'
    """Una sola vista por agente: dos estrategias distintas no tienen sentido."""

    _last: ViewResult | None = field(default=None, init=False, repr=False)
    _snapshot: RequestSnapshot | None = field(default=None, init=False, repr=False)

    @property
    def last_view(self) -> ViewResult | None:
        return self._last

    @property
    def last_request(self) -> RequestSnapshot | None:
        """Instrucciones y tools del último request, para el diagnóstico."""
        return self._snapshot

    def project(self, messages: Sequence[ModelMessage]) -> ViewResult:
        """Proyecta un historial con la estrategia, sin tocar el estado del run.

        Es lo que usa la vista de debug: quiere saber qué mandaría el agente
        *ahora*, no qué mandó en el request anterior.
        """
        return self.strategy.view(messages, self.meter.budget())

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        # `ModelRequestParameters` es la fuente de verdad de lo que se manda:
        # los instruction_parts ya resueltos y los schemas que el provider serializa.
        parameters = request_context.model_request_parameters
        instructions, tools = measure_request_parameters(parameters)
        self.meter.set_overhead(instructions=instructions, tools=tools)
        self._snapshot = snapshot_request(parameters)

        result = self.strategy.view(request_context.messages, self.meter.budget())
        # Asignar la secuencia es request-only; mutar un mensaje en su lugar
        # afectaría el historial persistente.
        request_context.messages = result.messages
        self._last = result
        if self.on_view is not None:
            # El diagnóstico nunca rompe el turno.
            with contextlib.suppress(Exception):
                self.on_view(result)
        return request_context
