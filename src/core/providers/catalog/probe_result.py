"""The ProbeResult value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProbeOutcome = Literal['valid', 'rejected', 'unknown']


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What the probe could establish."""

    outcome: ProbeOutcome
    detail: str = ''

    @property
    def is_rejected(self) -> bool:
        return self.outcome == 'rejected'

    @property
    def is_valid(self) -> bool:
        return self.outcome == 'valid'
