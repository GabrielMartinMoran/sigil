"""File bundled inside a skill."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SkillResource:
    """File bundled inside a skill."""

    path: str
    size: int
    kind: str
