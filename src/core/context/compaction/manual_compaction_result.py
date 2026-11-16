"""Result of an explicit context compaction."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ManualCompactionResult:
    status: Literal['complete', 'nothing_to_compact', 'not_reduced']
    before: int
    after: int
