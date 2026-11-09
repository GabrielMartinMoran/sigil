"""Append-only view with deterministic batches at pressure or instruction boundaries.

Ordinary operation appends. Pressure or a changed instruction prefix can reduce old
tool payloads and reasoning in one batch. Only pressure can also request a
model-written checkpoint when the reduced view remains above the target.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.coordinator import PolicyLookup
from src.engine.context.compaction.policy import DEFAULT_POLICY, CompactionPolicy
from src.engine.context.compaction.projection import finish, prepare
from src.engine.context.compaction.summary import Summary
from src.engine.context.strategy.constants import (
    DEFAULT_IMAGE_MAX_PERCENT,
    DEFAULT_IMAGE_TURNS,
)
from src.engine.context.strategy.view_result import ViewResult


@dataclass(frozen=True, slots=True)
class AppendOnlyStrategy:
    """Projects the history onto the model view without ageing anything out."""

    policy: CompactionPolicy = DEFAULT_POLICY

    provider: str | None = None
    """The provider the request goes to. Signed reasoning is only replayed to it."""

    image_turns: int = DEFAULT_IMAGE_TURNS
    """Rounds an image stays loaded, counting its own, once the hard budget is hit."""

    image_max_percent: int = DEFAULT_IMAGE_MAX_PERCENT
    """Ceiling for the pixels that stay, as a percentage of the model's window."""

    lookup: PolicyLookup | None = None
    """The compactor each tool declared, by name.

    ``None`` means every tool travels with the generic policy: the sizes shrink, and no
    tool says what it keeps. The engine always wires the catalogue; the default keeps a
    standalone strategy usable without one.
    """

    def with_provider(self, provider: str | None) -> AppendOnlyStrategy:
        """Signed reasoning is only replayed to the provider that produced it."""
        return replace(self, provider=provider)

    def view(
        self,
        messages: Sequence[ModelMessage],
        budget: ContextBudget,
        *,
        summary: Summary | None = None,
        compact_opportunistically: bool = False,
    ) -> ViewResult:
        prepared = prepare(
            messages,
            budget,
            policy=self.policy,
            lookup=self.lookup,
            provider=self.provider,
            image_turns=self.image_turns,
            image_percent=self.image_max_percent,
            summary=summary,
            compact_opportunistically=compact_opportunistically,
        )
        return finish(
            messages,
            prepared,
            budget,
            policy=self.policy,
            lookup=self.lookup,
        )
