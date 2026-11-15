"""A roadmap task."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from src.core.providers.domain.provider_account import now_iso
from src.core.roadmap.models.todo_status import TodoStatus


class TodoItem(BaseModel):
    """A roadmap task."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = Field(min_length=1)
    status: TodoStatus = 'pending'
    notes: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
