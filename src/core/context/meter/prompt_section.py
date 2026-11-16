"""The PromptSection value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptSection:
    """A sent history part's identity and cost, never its contents."""

    kind: str
    label: str
    tokens: int | None
