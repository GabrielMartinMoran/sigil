"""Nivel 2 del protocolo Agent Skills: metadatos más cuerpo del ``SKILL.md``."""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_resource import SkillResource


@dataclass(frozen=True, slots=True)
class Skill:
    """Nivel 2 del protocolo: metadatos + cuerpo del ``SKILL.md``."""

    metadata: SkillMetadata
    body: str
    resources: tuple[SkillResource, ...] = ()
