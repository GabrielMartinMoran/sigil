"""The RollbackOutcome value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RollbackOutcome:
    """What a rollback actually did, without optimism."""

    target_turn_id: str
    turns_dropped: int
    executions_dropped: int
    messages_dropped: int
    files_restored: int
    files_deleted: int
    files_unavailable: bool
    undo_checkpoint: str

    @property
    def touched_files(self) -> bool:
        return self.files_restored > 0 or self.files_deleted > 0
