"""Bridge between the question tool and the UI that knows how to ask.

The engine does not know the UI: the app registers an ``asker`` on mount and unregisters
it on unmount. Without an asker, the tool fails with a clear message instead of
waiting for an answer that is never going to arrive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem

QuestionAsker = Callable[[list[QuestionItem]], Awaitable[list[QuestionAnswer] | None]]
"""UI function that asks. ``None`` means the user cancelled."""


class QuestionHub:
    """Registers the UI function that knows how to ask the user."""

    def __init__(self) -> None:
        self._asker: QuestionAsker | None = None
        self._lock = asyncio.Lock()

    def set_asker(self, asker: QuestionAsker | None) -> None:
        self._asker = asker

    @property
    def available(self) -> bool:
        return self._asker is not None

    async def ask(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        """Asks serially, one at a time. Returns ``None`` if cancelled."""
        if self._asker is None:
            raise RuntimeError('Interactive questions are not available in this interface.')
        async with self._lock:
            return await self._asker(questions)
