"""Snapshot model for a roadmap."""

from pydantic import BaseModel

from src.core.roadmap.models.todo_item import TodoItem


class RoadmapSnapshot(BaseModel):
    """Full roadmap state."""

    items: list[TodoItem]
    counts: dict[str, int]
    persistence_error: str | None = None
