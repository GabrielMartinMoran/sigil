"""The requested resource does not exist or is outside the skill."""

from __future__ import annotations

from src.engine.skills.models.skill_error import SkillError


class SkillResourceError(SkillError):
    """The requested resource does not exist or is outside the skill."""
