"""Windowed view: the coordinator's projection of the history onto the model view.

The levers, cheapest to most destructive, and the reason for the order:

1. **Reasoning** past the window: a record, not working memory, and the bulkiest thing a
   long turn carries.
2. **Tool detail** past the level its age earns, arguments and results both: the payload
   is usually the result, and the path or the query lives in the argument.
3. **Whole turns**, only when the request still does not fit.

Items 1 and 2 are the compaction events of `context.compaction`; item 3 is the emergency,
and it is the only one that loses information the agent cannot get back without asking.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction import constants as tuning
from src.engine.context.compaction.constants import (
    REASONING_TARGET_PERCENT,
    REASONING_TARGET_STEPS,
)
from src.engine.context.compaction.coordinator import PolicyLookup
from src.engine.context.compaction.policy import DEFAULT_POLICY, CompactionPolicy
from src.engine.context.strategy.constants import (
    DEFAULT_IMAGE_MAX_PERCENT,
    DEFAULT_IMAGE_TURNS,
    DEFAULT_KEEP_RECENT_PROMPTS,
    PINNED_TOOLS,
)
from src.engine.context.strategy.view_result import ViewResult


@dataclass(frozen=True, slots=True)
class WindowedStrategy:
    """Projects the history through the compaction coordinator.

    The view is a pure function of the transcript, the policy and the provider: the same
    history produces the same view, and the events it reports are the boundaries that
    history crossed. That is what makes a resume, a rollback and a fork correct without
    storing a decision per message.
    """

    keep_recent_prompts: int = DEFAULT_KEEP_RECENT_PROMPTS
    """Turns kept intact before the budget starts discarding them."""

    reasoning_steps: int = REASONING_TARGET_STEPS
    """Operational target for reasoning responses retained across user turns."""

    reasoning_max_percent: int = REASONING_TARGET_PERCENT
    """Operational known-token target as a percentage of the model window."""

    provider: str | None = None
    """The provider the request goes to. Signed reasoning is only replayed to it."""

    image_turns: int = DEFAULT_IMAGE_TURNS
    """Rounds an image stays loaded, counting its own."""

    image_max_percent: int = DEFAULT_IMAGE_MAX_PERCENT
    """Ceiling for the pixels that stay, as a percentage of the model's window."""

    pinned_tools: frozenset[str] = PINNED_TOOLS
    """Skill reconstruction calls whose turns are discarded last under pressure."""

    lookup: PolicyLookup | None = None
    """The level of detail each tool declared, by name.

    ``None`` means every tool travels at the generic policy: the sizes shrink, and no
    tool says what it keeps. The engine always wires the catalogue; the default keeps a
    standalone strategy usable without one.
    """

    def policy(self) -> CompactionPolicy:
        """The coordinator rules this strategy asks for."""
        return replace(
            DEFAULT_POLICY,
            reasoning_steps=self.reasoning_steps,
            reasoning_trigger_steps=(
                tuning.REASONING_CEILING_STEPS
                if self.reasoning_steps == tuning.REASONING_TARGET_STEPS
                else max(1, self.reasoning_steps * tuning.REASONING_CEILING_MULTIPLIER)
            ),
            reasoning_max_percent=self.reasoning_max_percent,
            reasoning_trigger_percent=(
                tuning.REASONING_CEILING_PERCENT
                if self.reasoning_max_percent == tuning.REASONING_TARGET_PERCENT
                else min(
                    100,
                    max(
                        tuning.REASONING_CEILING_PERCENT,
                        self.reasoning_max_percent * tuning.REASONING_CEILING_MULTIPLIER,
                    ),
                )
            ),
            pinned_tools=self.pinned_tools,
        )

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        from src.engine.context.compaction.projection import finish, prepare

        policy = self.policy()
        prepared = prepare(
            messages,
            budget,
            policy=policy,
            lookup=self.lookup,
            provider=self.provider,
            image_turns=self.image_turns,
            image_percent=self.image_max_percent,
        )
        return finish(
            messages,
            prepared,
            budget,
            policy=policy,
            lookup=self.lookup,
            provider=self.provider,
            keep_recent=self.keep_recent_prompts,
        )
