"""Acuse de carga de un skill (nivel 2)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.engine.skills.tool_models.skill_resource_info import SkillResourceInfo


class LoadedSkill(BaseModel):
    """Acuse de carga de un skill (nivel 2).

    El cuerpo del `SKILL.md` **no** viaja acá: se inyecta como bloque de
    instrucciones para que sobreviva a la compactación y no compita por el
    presupuesto del historial. Este resultado sólo confirma la carga y lista los
    recursos que se pueden leer on demand.
    """

    name: str
    description: str
    source: str
    path: str
    resources: list[SkillResourceInfo]
    note: str = Field(description='Where the skill body went and what to do next.')
