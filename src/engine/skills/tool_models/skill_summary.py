"""Metadata of an installed skill, as the model sees it."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillSummary(BaseModel):
    """Metadata of an installed skill."""

    name: str
    description: str
    source: str = Field(description='Where the skill was found: project, global or custom.')
    path: str
    license: str | None = None
    compatibility: str | None = None
    model_invocable: bool = True
