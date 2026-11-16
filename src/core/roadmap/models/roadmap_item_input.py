"""Input model for a roadmap task."""

from pydantic import BaseModel, Field

from src.core.roadmap.models.todo_status import TodoStatus


class RoadmapItemInput(BaseModel):
    """An input task for the roadmap."""

    title: str = Field(min_length=1)
    id: str | None = None
    status: TodoStatus = 'pending'
    notes: str | None = None
