"""Un bloque de instrucciones con su política de cambio."""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.context.instructions.constants import InstructionChangePolicy


@dataclass(frozen=True, slots=True)
class InstructionBlock:
    """Un bloque de instrucciones con su política de cambio."""

    name: str
    content: str
    on_change: InstructionChangePolicy = 'rewrite'
    source: str | None = None
    """De dónde salió, para diagnóstico. No se manda al modelo."""
