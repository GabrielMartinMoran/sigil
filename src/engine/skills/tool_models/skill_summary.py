"""Metadatos de un skill instalado, tal como los ve el modelo."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillSummary(BaseModel):
    """Metadatos de un skill instalado."""

    name: str
    description: str
    source: str = Field(description='Where the skill was found: project, global or custom.')
    path: str
    license: str | None = None
    compatibility: str | None = None
    model_invocable: bool = True
