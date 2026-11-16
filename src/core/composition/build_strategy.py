"""The build_strategy helper of the core layer."""

from __future__ import annotations

from src.core.config.sigil_settings import SigilSettings
from src.core.context.compaction.compaction_policy import CompactionPolicy
from src.core.context.compaction.coordinator import PolicyLookup
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from src.core.context.strategy.context_strategy import ContextStrategy
from src.core.context.strategy.passthrough_strategy import PassthroughStrategy


def build_strategy(settings: SigilSettings, *, lookup: PolicyLookup | None = None) -> ContextStrategy:
    """View strategy, with the compactor each tool declared.

    - `append-only` (default): the history grows untouched and is rewritten only when the
      projected input crosses the trigger; a summary stage covers what reduction cannot.
    - `passthrough`: sends the history as is, for diagnostics.
    """
    if settings.context_strategy == 'passthrough':
        return PassthroughStrategy()
    return AppendOnlyStrategy(
        policy=CompactionPolicy(
            trigger_percent=settings.context_trigger_percent,
            target_percent=settings.context_target_percent,
            recent_reasoning_blocks=settings.context_recent_reasoning_blocks,
            recent_tool_calls=settings.context_recent_tool_calls,
        ),
        image_turns=settings.image_turns,
        image_max_percent=settings.image_max_percent,
        lookup=lookup,
    )
