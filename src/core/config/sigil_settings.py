"""The SigilSettings value of the core layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.context.compaction.compaction_policy import DEFAULT_POLICY
from src.core.context.context_budget import DEFAULT_OUTPUT_RESERVE, DEFAULT_SAFETY_MARGIN_PERCENT
from src.core.context.strategy.constants import (
    DEFAULT_IMAGE_MAX_PERCENT,
    DEFAULT_IMAGE_TURNS,
)

DEFAULT_MAX_STEPS = 256


DEFAULT_MODEL_MAX_ATTEMPTS = 5


CONTEXT_STRATEGIES = ('append-only', 'passthrough')


DEFAULT_CONTEXT_STRATEGY = 'append-only'


@dataclass(frozen=True, slots=True)
class SigilSettings:
    """Settings of a session. Immutable on purpose: a turn never mutates them."""

    max_steps: int = DEFAULT_MAX_STEPS
    model_max_attempts: int = DEFAULT_MODEL_MAX_ATTEMPTS
    context_strategy: str = DEFAULT_CONTEXT_STRATEGY
    context_trigger_percent: int = DEFAULT_POLICY.trigger_percent
    """Share of the window above which deterministic compaction runs."""
    context_target_percent: int = DEFAULT_POLICY.target_percent
    """Share of the window deterministic compaction should leave behind."""
    context_recent_reasoning_blocks: int = DEFAULT_POLICY.recent_reasoning_blocks
    context_recent_tool_calls: int = DEFAULT_POLICY.recent_tool_calls
    context_safety_margin_percent: int = DEFAULT_SAFETY_MARGIN_PERCENT
    image_turns: int = DEFAULT_IMAGE_TURNS
    """Rounds an image stays loaded, counting the one it arrived in."""
    image_max_percent: int = DEFAULT_IMAGE_MAX_PERCENT
    """Ceiling for the pixels that stay, as a percentage of the model window."""
    output_reserve: int = DEFAULT_OUTPUT_RESERVE

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError('max_steps must be at least 1')
        if self.model_max_attempts < 1:
            raise ValueError('model_max_attempts must be at least 1')
        if self.context_strategy not in CONTEXT_STRATEGIES:
            supported = ', '.join(CONTEXT_STRATEGIES)
            raise ValueError(f'context_strategy must be one of {supported}; got {self.context_strategy!r}')
        if not 0 < self.context_target_percent < self.context_trigger_percent <= 100:
            raise ValueError('context percentages must satisfy 0 < target < trigger <= 100')
        if self.context_recent_reasoning_blocks < 0 or self.context_recent_tool_calls < 0:
            raise ValueError('context retention counts must be non-negative')
        if not 0 <= self.context_safety_margin_percent < 100:
            raise ValueError('context_safety_margin_percent must be between 0 and 99')
        if self.image_turns < 1:
            raise ValueError('image_turns must be at least 1: the turn in progress always travels')
        if self.image_max_percent < 0:
            raise ValueError('image_max_percent cannot be negative')
        if self.output_reserve < 0:
            raise ValueError('output_reserve cannot be negative')
