"""Load acknowledgement of a skill (level 2)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.engine.skills.tool_models.skill_resource_info import SkillResourceInfo


class LoadedSkill(BaseModel):
    """Load acknowledgement of a skill (level 2).

    The `SKILL.md` body does **not** travel here: it is injected as an
    instruction block so it survives compaction and does not compete for the
    history budget. This result only confirms the load and lists the
    resources that can be read on demand.
    """

    name: str
    description: str
    source: str
    path: str
    resources: list[SkillResourceInfo]
    note: str = Field(description='Where the skill body went and what to do next.')
