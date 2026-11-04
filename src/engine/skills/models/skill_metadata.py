"""Nivel 1 del protocolo Agent Skills: metadatos baratos de un skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Nivel 1 del protocolo: metadatos baratos de un skill.

    Es lo único que se inyecta siempre en el contexto del modelo.
    """

    name: str
    description: str
    path: Path
    skill_file: Path
    source: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: tuple[str, ...] = ()
    model_invocable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
