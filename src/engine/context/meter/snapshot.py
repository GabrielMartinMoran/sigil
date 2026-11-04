"""Los dos números de contexto y el presupuesto vigente."""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.context.budget import ContextBudget


def percent(used: int | None, window: int | None) -> float | None:
    """Porcentaje de ventana usada, o ``None`` si falta información."""
    if not window or used is None:
        return None
    return max(0.0, used / window * 100.0)


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Los dos números de contexto y el presupuesto vigente."""

    measured: int | None
    projected: int | None
    window: int | None
    budget: ContextBudget

    @property
    def measured_percent(self) -> float | None:
        return percent(self.measured, self.window)

    @property
    def projected_percent(self) -> float | None:
        return percent(self.projected, self.window)
