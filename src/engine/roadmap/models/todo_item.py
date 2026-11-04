"""A roadmap task."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from src.engine.roadmap.models.todo_status import TodoStatus


def now_iso() -> str:
    """ISO 8601 timestamp, in UTC."""
    return datetime.now(UTC).isoformat()


class TodoItem(BaseModel):
    """A roadmap task."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = Field(min_length=1)
    status: TodoStatus = 'pending'
    notes: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
