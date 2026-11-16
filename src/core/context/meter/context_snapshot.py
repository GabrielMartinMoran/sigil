"""The ContextSnapshot value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.context.context_budget import ContextBudget
from src.core.context.meter.percent import percent


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """The two context numbers and the current budget."""

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
