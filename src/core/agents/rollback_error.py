"""The RollbackError value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RollbackError(Exception):
    """A rollback that could not start, with the reason said plainly."""

    reason: str

    def __str__(self) -> str:
        return self.reason
