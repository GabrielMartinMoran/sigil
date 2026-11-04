"""No existe un skill con ese nombre."""

from __future__ import annotations

from src.engine.skills.models.skill_error import SkillError


class SkillNotFound(SkillError):
    """No existe un skill con ese nombre."""
