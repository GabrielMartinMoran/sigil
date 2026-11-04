"""The ``SKILL.md`` does not follow the protocol format."""

from __future__ import annotations

from src.engine.skills.models.skill_error import SkillError


class SkillParseError(SkillError):
    """The ``SKILL.md`` does not follow the protocol format."""
