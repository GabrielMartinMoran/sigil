"""El ``SKILL.md`` no cumple el formato del protocolo."""

from __future__ import annotations

from src.engine.skills.models.skill_error import SkillError


class SkillParseError(SkillError):
    """El ``SKILL.md`` no cumple el formato del protocolo."""
