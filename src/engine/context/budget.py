"""Presupuesto de contexto: reservas nombradas sobre la ventana del modelo.

La ventana no es toda para el historial. Antes de repartir hay que descontar lo
que se manda siempre y lo que el modelo necesita para responder:

    ventana
      − tools (schemas)          ← medido, suele ser el bloque más grande
      − instructions (bloques)   ← medido
      − output (max_tokens)      ← reservado
      = disponible para historial
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_OUTPUT_RESERVE = 8_000
"""Tokens que se dejan libres para la respuesta del modelo."""


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Reparto de la ventana de contexto entre bloques fijos e historial."""

    window: int | None
    """Ventana del modelo, o ``None`` si se desconoce."""

    tools: int = 0
    """Tokens de los schemas de tools."""

    instructions: int = 0
    """Tokens de los bloques de instrucciones."""

    output_reserve: int = DEFAULT_OUTPUT_RESERVE
    """Tokens reservados para la respuesta del modelo."""

    @property
    def fixed(self) -> int:
        """Lo que se manda en cada request sin importar la conversación."""
        return self.tools + self.instructions

    @property
    def available_for_history(self) -> int | None:
        """Tokens que quedan para el historial, o ``None`` si no hay ventana."""
        if self.window is None:
            return None
        return max(0, self.window - self.fixed - self.output_reserve)

    def fits(self, history_tokens: int) -> bool | None:
        """Si un historial entra en el presupuesto. ``None`` si no hay ventana."""
        available = self.available_for_history
        if available is None:
            return None
        return history_tokens <= available

    def with_measurements(self, *, tools: int | None = None, instructions: int | None = None) -> ContextBudget:
        """Copia con las mediciones actualizadas."""
        return ContextBudget(
            window=self.window,
            tools=self.tools if tools is None else tools,
            instructions=self.instructions if instructions is None else instructions,
            output_reserve=self.output_reserve,
        )
