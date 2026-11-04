"""Convierte errores de tools en resultados fallidos que el modelo puede leer.

Sin esto, una excepción de una tool —un `patch_file` que no encuentra el
`old_string`, un `read_file` sobre una ruta inexistente— aborta el run entero:
el turno muere, el usuario recupera el control y el agente nunca se entera de
qué falló ni puede corregirse.

pydantic-ai deja pasar la excepción por defecto. `ToolFailed` la convierte en un
`ToolReturnPart` con `outcome='failed'` que el modelo ve en el siguiente paso.
A diferencia de `ModelRetry`, no consume el presupuesto de reintentos de la tool
ni le pide al modelo que repita la misma llamada: le informa el fallo para que
adapte el plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import AgentDepsT, RunContext, ToolDefinition

MAX_ERROR_CHARS = 2000
"""Tope del mensaje que se le devuelve al modelo, para no inflar el contexto."""


def describe_tool_error(error: BaseException) -> str:
    """Mensaje de una tool fallida, con el tipo de excepción adelante."""
    detail = ' '.join(str(error).split()) or '(no detail)'
    if len(detail) > MAX_ERROR_CHARS:
        detail = detail[: MAX_ERROR_CHARS - 1] + '…'
    return f'{type(error).__name__}: {detail}'


@dataclass
class ToolErrorRecovery(AbstractCapability[AgentDepsT]):
    """Devuelve el error de una tool al modelo en vez de tumbar el turno."""

    id: str | None = 'tool_error_recovery'

    async def on_tool_execute_error(
        self,
        ctx: RunContext[AgentDepsT],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        error: Exception,
    ) -> Any:
        raise ToolFailed(describe_tool_error(error)) from error


__all__ = ['MAX_ERROR_CHARS', 'ToolErrorRecovery', 'describe_tool_error']
