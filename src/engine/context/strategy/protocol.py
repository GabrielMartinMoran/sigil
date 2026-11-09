"""Contract of a history view strategy."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.summary import Summary
from src.engine.context.strategy.view_result import ViewResult


class ContextStrategy(Protocol):
    """Projects the session log onto what gets sent to the model."""

    def view(
        self,
        messages: Sequence[ModelMessage],
        budget: ContextBudget,
        *,
        summary: Summary | None = None,
        compact_opportunistically: bool = False,
    ) -> ViewResult:
        """``summary`` is a checkpoint the caller already obtained, if any."""
        ...

    def with_provider(self, provider: str | None) -> ContextStrategy:
        """The same strategy, pointed at the provider the next request goes to."""
        ...
