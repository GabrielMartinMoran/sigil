"""Strategy that sends the history as is."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.summary import Summary
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens


@dataclass(frozen=True, slots=True)
class PassthroughStrategy:
    """Sends the history as is. It is the behavior before this module."""

    def with_provider(self, provider: str | None) -> PassthroughStrategy:
        del provider
        return self

    def view(
        self,
        messages: Sequence[ModelMessage],
        budget: ContextBudget,
        *,
        summary: Summary | None = None,
        compact_opportunistically: bool = False,
    ) -> ViewResult:
        # A summary is only ever produced for a strategy that asks for one, so this
        # strategy has nothing to apply and nothing to report.
        del summary, budget, compact_opportunistically
        view = list(messages)
        return ViewResult(messages=view, estimated_tokens=estimate_messages_tokens(view))
