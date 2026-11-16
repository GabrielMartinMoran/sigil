"""The ToolRow value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolRow:
    """A tool schema as it was sent."""

    name: str
    tokens: int
    deferred: bool = False
    """Whether the schema was deferred (it does not travel until the history reveals it)."""
