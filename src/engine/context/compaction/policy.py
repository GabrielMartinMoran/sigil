"""Thresholds and retention counts for pressure-driven compaction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    """When to compact, and how much of the recent work is never touched.

    The trigger is a share of the model's total projected input: instructions, tool
    schemas and history together. The target is what deterministic reduction should
    leave behind; above it, the summary stage runs. Both are starting hypotheses, not
    measured quality boundaries.
    """

    trigger_percent: int = 75
    """Share of the window above which deterministic compaction runs."""

    target_percent: int = 60
    """Share of the window deterministic compaction should leave behind."""

    recent_reasoning_blocks: int = 5
    """Reasoning parts always kept verbatim, counting individual blocks."""

    recent_tool_calls: int = 20
    """Tool calls always kept verbatim, counting parallel calls individually."""

    def __post_init__(self) -> None:
        if not 0 < self.target_percent < self.trigger_percent <= 100:
            raise ValueError('compaction thresholds must satisfy 0 < target < trigger <= 100')
        if self.recent_reasoning_blocks < 0 or self.recent_tool_calls < 0:
            raise ValueError('retention counts must be non-negative')


DEFAULT_POLICY = CompactionPolicy()
