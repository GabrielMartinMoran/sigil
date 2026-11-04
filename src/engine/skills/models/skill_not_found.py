"""There is no skill with that name."""

from __future__ import annotations

from src.engine.skills.models.skill_error import SkillError


class SkillNotFound(SkillError):
    """There is no skill with that name."""
