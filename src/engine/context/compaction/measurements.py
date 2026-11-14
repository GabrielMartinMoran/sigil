"""Measurements owned by one synchronous context projection.

Projection stages create new sequences but never mutate their messages. Identity
keys therefore allow reuse within a view, not across requests: streaming, rollback,
and provider changes must always start with fresh measurements.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.input_estimate import InputEstimate, estimate_input
from src.engine.context.tokens import estimate_messages_tokens


class ProjectionMeasurements:
    """Reuse counts and input estimates while retaining every keyed message."""

    def __init__(self) -> None:
        self._sequences: dict[tuple[int, ...], tuple[ModelMessage, ...]] = {}
        self._tokens: dict[tuple[int, ...], int] = {}
        self._inputs: dict[tuple[tuple[int, ...], ContextBudget], InputEstimate] = {}

    def _key(self, messages: Sequence[ModelMessage]) -> tuple[int, ...]:
        key = tuple(map(id, messages))
        if key not in self._sequences:
            # Strong references prevent id reuse after an intermediate view is dropped.
            self._sequences[key] = tuple(messages)
        return key

    def tokens(self, messages: Sequence[ModelMessage]) -> int:
        key = self._key(messages)
        if key not in self._tokens:
            self._tokens[key] = estimate_messages_tokens(messages)
        return self._tokens[key]

    def input(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> InputEstimate:
        key = (self._key(messages), budget)
        if key not in self._inputs:
            self._inputs[key] = estimate_input(messages, budget)
        return self._inputs[key]
