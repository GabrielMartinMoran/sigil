"""The ForkOutcome value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ForkOutcome:
    """What a fork produced."""

    session_id: str
    turns_copied: int
    messages_copied: int
    executions_copied: int
