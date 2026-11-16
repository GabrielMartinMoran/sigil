"""The StepTiming value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StepTiming:
    phase: str
    seconds: float
    first_event: float | None
    interrupted: bool
