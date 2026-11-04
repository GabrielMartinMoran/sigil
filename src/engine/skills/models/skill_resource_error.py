"""El recurso pedido no existe o está fuera del skill."""

from __future__ import annotations

from src.engine.skills.models.skill_error import SkillError


class SkillResourceError(SkillError):
    """El recurso pedido no existe o está fuera del skill."""
