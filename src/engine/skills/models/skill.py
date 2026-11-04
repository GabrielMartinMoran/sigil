"""Level 2 of the Agent Skills protocol: metadata plus the ``SKILL.md`` body."""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_resource import SkillResource


@dataclass(frozen=True, slots=True)
class Skill:
    """Level 2 of the protocol: metadata + the ``SKILL.md`` body."""

    metadata: SkillMetadata
    body: str
    resources: tuple[SkillResource, ...] = ()
