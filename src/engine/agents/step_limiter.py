"""Hard step limit per turn.

Every model response (message) and every tool call count as one
step. When the configured limit is exceeded, `StepLimitExceeded` is raised, which
aborts the agent run before executing the step that would exceed the limit.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import FunctionToolCallEvent, PartStartEvent


class StepLimitExceeded(Exception):
    """Raised when a turn exceeds the maximum allowed number of steps."""

    def __init__(self, limit: int) -> None:
        super().__init__(f'Step limit of {limit} reached in this turn.')
        self.limit = limit


class StepLimiter:
    """Counts the steps of a turn and aborts when the limit is exceeded.

    A step is:
    - every model response (a `PartStartEvent` with index 0), or
    - every tool call (a `FunctionToolCallEvent`).
    """

    def __init__(self, max_steps: int) -> None:
        if max_steps < 1:
            raise ValueError('max_steps must be >= 1')
        self.max_steps = max_steps
        self.steps = 0

    def count(self, event: Any) -> None:
        """Records the event and raises `StepLimitExceeded` if the limit is passed."""
        if isinstance(event, PartStartEvent) and event.index == 0 or isinstance(event, FunctionToolCallEvent):
            self._increment()

    def _increment(self) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise StepLimitExceeded(self.max_steps)
