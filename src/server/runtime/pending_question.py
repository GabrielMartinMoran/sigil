"""A question waiting for an answer from a client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.core.interaction.models.question_answer import QuestionAnswer
from src.core.interaction.models.question_item import QuestionItem


@dataclass
class PendingQuestion:
    """A question asked by the agent, waiting for an answer from a browser."""

    id: str
    questions: list[QuestionItem]
    future: asyncio.Future[list[QuestionAnswer] | None] = field(
        default_factory=lambda: asyncio.get_event_loop().create_future()
    )
