"""Result of a storage maintenance pass."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """What a pass actually did."""

    blobs_collected: int
    checkpoints_dropped: int
    bytes_before: int
    bytes_after: int

    @property
    def reclaimed(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)
