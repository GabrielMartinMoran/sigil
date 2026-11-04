"""Archivo empaquetado dentro de un skill."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SkillResource:
    """Archivo empaquetado dentro de un skill."""

    path: str
    size: int
    kind: str
