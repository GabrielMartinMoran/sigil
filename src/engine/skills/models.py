"""Modelos de datos del protocolo Agent Skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SkillError(Exception):
    """Error base del subsistema de skills."""


class SkillParseError(SkillError):
    """El ``SKILL.md`` no cumple el formato del protocolo."""


class SkillNotFound(SkillError):
    """No existe un skill con ese nombre."""


class SkillResourceError(SkillError):
    """El recurso pedido no existe o está fuera del skill."""


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


@dataclass(frozen=True, slots=True)
class SkillResource:
    """Archivo empaquetado dentro de un skill."""

    path: str
    size: int
    kind: str


@dataclass(frozen=True, slots=True)
class Skill:
    """Nivel 2 del protocolo: metadatos + cuerpo del ``SKILL.md``."""

    metadata: SkillMetadata
    body: str
    resources: tuple[SkillResource, ...] = ()
