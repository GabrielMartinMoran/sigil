"""Operational ceilings and targets for coordinated context maintenance."""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.context.compaction import constants
from src.engine.context.strategy.constants import PINNED_TOOLS
from src.engine.tooling.lod import ToolLOD


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    """Thresholds are inclusive; age makes a call eligible, not immediately reduced."""

    reasoning_steps: int = constants.REASONING_TARGET_STEPS
    reasoning_trigger_steps: int = constants.REASONING_CEILING_STEPS
    reasoning_max_percent: int = constants.REASONING_TARGET_PERCENT
    reasoning_trigger_percent: int = constants.REASONING_CEILING_PERCENT
    lod_ages: tuple[int, int, int] = constants.TOOL_LOD_AGES_STEPS
    tool_trigger_chars: int = constants.TOOL_PENDING_REDUCTION_CEILING_CHARS
    tool_target_chars: int = constants.TOOL_PENDING_REDUCTION_TARGET_CHARS
    pinned_tools: frozenset[str] = PINNED_TOOLS

    def __post_init__(self) -> None:
        if self.reasoning_steps < 0 or self.reasoning_trigger_steps <= self.reasoning_steps:
            raise ValueError('reasoning ceiling must exceed its non-negative target')
        if not 0 <= self.reasoning_max_percent <= self.reasoning_trigger_percent <= 100:
            raise ValueError('reasoning percentages must satisfy 0 <= target <= ceiling <= 100')
        if len(self.lod_ages) != 3 or tuple(sorted(self.lod_ages)) != self.lod_ages or self.lod_ages[0] < 0:
            raise ValueError('LOD ages must be non-negative and ordered')
        if not 0 <= self.tool_target_chars < self.tool_trigger_chars:
            raise ValueError('tool ceiling must exceed its non-negative target')

    def lod_for_age(self, age: int) -> ToolLOD:
        """The maximum eligible level; application waits for a maintenance event."""
        return ToolLOD(sum(age >= fence for fence in self.lod_ages))


DEFAULT_POLICY = CompactionPolicy()
