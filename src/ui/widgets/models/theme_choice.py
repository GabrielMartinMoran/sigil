"""One theme the picker offers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemeChoice:
    """The theme the user settled on."""

    name: str
