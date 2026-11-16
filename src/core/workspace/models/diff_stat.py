"""Working-tree diff summary."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiffStat:
    """Additions and deletions of the working tree against `HEAD`."""

    added: int = 0
    removed: int = 0
    files: int = 0
    untracked: int = 0

    @property
    def total(self) -> int:
        """Changed lines. Zero means the tracked tree matches `HEAD`."""
        return self.added + self.removed

    @property
    def is_empty(self) -> bool:
        return self.total == 0 and self.untracked == 0
