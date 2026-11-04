"""Límite duro de pasos por turno.

Cada respuesta del modelo (mensaje) y cada llamada a una tool cuentan como un
paso. Cuando se supera el límite configurado se lanza `StepLimitExceeded`, que
aborta el run del agente antes de ejecutar el paso que excedería el límite.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import FunctionToolCallEvent, PartStartEvent


class StepLimitExceeded(Exception):
    """Se lanza cuando un turno supera la cantidad máxima de pasos permitidos."""

    def __init__(self, limit: int) -> None:
        super().__init__(f'Step limit of {limit} reached in this turn.')
        self.limit = limit


class StepLimiter:
    """Cuenta pasos de un turno y aborta al superar el límite.

    Un paso es:
    - cada respuesta del modelo (un `PartStartEvent` de índice 0), o
    - cada llamada a una tool (un `FunctionToolCallEvent`).
    """

    def __init__(self, max_steps: int) -> None:
        if max_steps < 1:
            raise ValueError('max_steps debe ser >= 1')
        self.max_steps = max_steps
        self.steps = 0

    def count(self, event: Any) -> None:
        """Registra el evento y lanza `StepLimitExceeded` si se pasa del límite."""
        if isinstance(event, PartStartEvent) and event.index == 0 or isinstance(event, FunctionToolCallEvent):
            self._increment()

    def _increment(self) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise StepLimitExceeded(self.max_steps)
