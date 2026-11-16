"""One model the picker offers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """The model the user picked."""

    model: str
    from_catalog: bool = True
