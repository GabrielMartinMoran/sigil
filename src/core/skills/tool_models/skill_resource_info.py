"""File bundled inside a skill, as the model sees it."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillResourceInfo(BaseModel):
    """File bundled inside a skill."""

    path: str
    size: int
    kind: str = Field(description='scripts, reference, asset or other.')
