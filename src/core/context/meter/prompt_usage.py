"""The PromptUsage value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptUsage:
    """Cost of one turn of the conversation."""

    index: int
    label: str
    tokens: int
    messages: int
