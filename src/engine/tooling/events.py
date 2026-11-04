"""Eventos que emite el wrapper de una tool.

Un evento describe un momento de la vida de una llamada —arrancó, terminó bien,
falló— con lo mínimo que necesita un sink para mostrarlo: nombre, argumentos,
duración y un resumen del resultado o del error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ToolPhase = Literal['start', 'success', 'error']
"""Fase de una llamada: arrancó, terminó bien o falló."""

PREVIEW_LIMIT = 200
"""Caracteres que se conservan en un resumen de una línea."""


def preview(value: Any, limit: int = PREVIEW_LIMIT) -> str:
    """Resumen de una línea, seguro para cualquier valor.

    El logging nunca debe romper una tool, así que un ``__str__`` que explota
    cae al ``repr`` del objeto en vez de propagar la excepción.
    """
    try:
        text = ' '.join(str(value).split())
    except Exception:  # noqa: BLE001 - el logging nunca debe romper una tool
        text = object.__repr__(value)
    return text if len(text) <= limit else text[: limit - 1] + '…'


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """Evento emitido por el wrapper de una tool."""

    tool_name: str
    phase: ToolPhase
    args: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None
    result_preview: str | None = None
    error: str | None = None
