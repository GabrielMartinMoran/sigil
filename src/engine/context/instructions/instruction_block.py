"""An instruction block with its change policy."""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.context.instructions.constants import InstructionChangePolicy


@dataclass(frozen=True, slots=True)
class InstructionBlock:
    """An instruction block with its change policy."""

    name: str
    content: str
    on_change: InstructionChangePolicy = 'rewrite'
    source: str | None = None
    """Where it came from, for diagnostics. It is not sent to the model."""
