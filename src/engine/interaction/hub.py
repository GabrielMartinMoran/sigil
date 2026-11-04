"""Puente entre la tool de preguntas y la UI que sabe preguntar.

El engine no conoce la UI: la app registra un ``asker`` al montar y lo da de
baja al desmontar. Sin asker, la tool falla con un mensaje claro en vez de
quedarse esperando una respuesta que nunca va a llegar.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from src.engine.interaction.models import QuestionAnswer, QuestionItem

QuestionAsker = Callable[[list[QuestionItem]], Awaitable[list[QuestionAnswer] | None]]
"""Función de la UI que pregunta. ``None`` significa que el usuario canceló."""


class QuestionHub:
    """Registra la función de la UI que sabe preguntar al usuario."""

    def __init__(self) -> None:
        self._asker: QuestionAsker | None = None
        self._lock = asyncio.Lock()

    def set_asker(self, asker: QuestionAsker | None) -> None:
        self._asker = asker

    @property
    def available(self) -> bool:
        return self._asker is not None

    async def ask(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        """Pregunta en serie, de a una. Devuelve ``None`` si se cancela."""
        if self._asker is None:
            raise RuntimeError('Interactive questions are not available in this interface.')
        async with self._lock:
            return await self._asker(questions)
