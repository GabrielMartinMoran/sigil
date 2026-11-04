"""Guardas de concurrencia para tools que se llaman en paralelo.

pydantic-ai ejecuta las llamadas hermanas de una misma respuesta del modelo como
tareas concurrentes. Para casi todas las tools eso está bien. Para las que mutan
un recurso compartido, dos llamadas simultáneas se pisan: cada una lee el estado
original y la última en escribir borra el trabajo de la anterior.

Una guarda declara, por tool, cuándo una llamada no puede convivir con las
llamadas anteriores de la misma respuesta. Recibe el contexto de la invocación
—incluidas esas llamadas anteriores, en orden de emisión— y devuelve el motivo
del conflicto, o ``None`` si la llamada puede correr en paralelo.

La guarda se evalúa en orden de invocación: la primera llamada de un grupo en
conflicto siempre pasa, y las siguientes fallan con un mensaje accionable. El
modelo ve el fallo como resultado de la tool y puede reemitir la llamada en un
paso aparte.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart


@dataclass(frozen=True, slots=True)
class SiblingCall:
    """Una llamada emitida antes que la actual en la misma respuesta."""

    tool_name: str
    args: dict[str, Any]
    tool_call_id: str | None


@dataclass(frozen=True, slots=True)
class ConcurrencyContext:
    """Lo que ve una guarda al decidir si la llamada puede correr en paralelo."""

    tool_name: str
    """Nombre de la tool que se está por ejecutar."""

    args: dict[str, Any]
    """Argumentos efectivos de la llamada, con los defaults aplicados."""

    tool_call_id: str | None
    """Id de la llamada actual, si el modelo o el framework lo asignaron."""

    siblings: tuple[SiblingCall, ...]
    """Llamadas emitidas antes que ésta en la misma respuesta, en orden."""


ConcurrencyGuard = Callable[[ConcurrencyContext], str | None]
"""Devuelve el motivo del conflicto, o ``None`` si la llamada puede correr."""


class ToolConflict(Exception):
    """La llamada no puede ejecutarse junto a sus hermanas de la misma respuesta."""


def _as_sibling(part: ToolCallPart) -> SiblingCall:
    return SiblingCall(
        tool_name=part.tool_name,
        args=part.args if isinstance(part.args, dict) else {},
        tool_call_id=part.tool_call_id,
    )


def _current_index(
    parts: Sequence[ToolCallPart],
    tool_call_id: str | None,
    tool_name: str,
    args: dict[str, Any],
) -> int | None:
    """Posición de la llamada actual dentro de la respuesta."""
    if tool_call_id is not None:
        for index, part in enumerate(parts):
            if part.tool_call_id == tool_call_id:
                return index
    # Sin id (o con un id que no aparece) se cae al primer match por nombre y
    # argumentos. Es una heurística, pero sólo decide qué llamadas se consideran
    # anteriores: equivocarse de más hace fallar la guarda, nunca perder datos.
    for index, part in enumerate(parts):
        if part.tool_name == tool_name and part.args == args:
            return index
    return None


def earlier_siblings(
    messages: Sequence[ModelMessage],
    *,
    tool_call_id: str | None,
    tool_name: str,
    args: dict[str, Any],
) -> tuple[SiblingCall, ...]:
    """Llamadas anteriores a la actual en la respuesta que las originó.

    Durante la ejecución de una tool, el último mensaje del run es la
    ``ModelResponse`` que pidió todas las llamadas del lote. Si no lo es —o si
    la llamada actual no aparece— se devuelve una tupla vacía: sin información
    de hermanos, la guarda no tiene con qué comparar.
    """
    if not messages:
        return ()
    last = messages[-1]
    if not isinstance(last, ModelResponse):
        return ()
    parts = [part for part in last.parts if isinstance(part, ToolCallPart)]
    index = _current_index(parts, tool_call_id, tool_name, args)
    if index is None:
        return ()
    return tuple(_as_sibling(part) for part in parts[:index])


__all__ = [
    'ConcurrencyContext',
    'ConcurrencyGuard',
    'SiblingCall',
    'ToolConflict',
    'earlier_siblings',
]
