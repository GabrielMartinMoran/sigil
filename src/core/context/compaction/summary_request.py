"""What a summary would cover, for a caller that can reach a model."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """What a summary would cover, for a caller that can reach a model."""

    messages: tuple[ModelMessage, ...]
    boundary: str
    previous: str | None = None
