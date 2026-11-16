"""One instant a session can be returned to."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One instant a session can be returned to."""

    id: str
    session_id: str
    execution_id: str
    turn_id: str | None
    at: str
    reason: str
    label: str
    tree_commit: str | None
