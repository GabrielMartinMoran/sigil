"""Archivo empaquetado dentro de un skill, tal como lo ve el modelo."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillResourceInfo(BaseModel):
    """Archivo empaquetado dentro de un skill."""

    path: str
    size: int
    kind: str = Field(description='scripts, reference, asset or other.')
