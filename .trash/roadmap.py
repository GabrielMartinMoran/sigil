"""Tool para gestionar el roadmap de la tarea actual."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.engine.roadmap.store import RoadmapStore, TodoItem, TodoStatus
from src.engine.tools.tool_registry import ToolLogger, ToolRegistrar


class RoadmapItemInput(BaseModel):
    """Una tarea de entrada para el roadmap."""

    title: str = Field(min_length=1)
    id: str | None = None
    status: TodoStatus = 'pending'
    notes: str | None = None


class RoadmapSnapshot(BaseModel):
    """Estado completo del roadmap."""

    items: list[TodoItem]
    counts: dict[str, int]
    persistence_error: str | None = None


def _coerce(item: RoadmapItemInput) -> TodoItem:
    data: dict[str, Any] = {'title': item.title, 'status': item.status, 'notes': item.notes}
    if item.id:
        data['id'] = item.id
    return TodoItem(**data)


def _snapshot(store: RoadmapStore) -> RoadmapSnapshot:
    return RoadmapSnapshot(
        items=store.snapshot(),
        counts=store.counts(),
        persistence_error=store.persistence_error,
    )


def register_roadmap_tools(agent: Any, store: RoadmapStore, logger: ToolLogger | None = None) -> None:
    """Registra la tool de roadmap en un agente de pydantic-ai."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool
    def update_roadmap(
        action: Literal['set', 'add', 'update', 'remove', 'clear'],
        items: list[RoadmapItemInput] | None = None,
        item_id: str | None = None,
        title: str | None = None,
        status: TodoStatus | None = None,
        notes: str | None = None,
    ) -> RoadmapSnapshot:
        """Set or update the task roadmap shown in the UI sidebar.

        Use this to plan multi-step work and keep the user informed. The
        roadmap is a flat list of tasks with a status: `pending`, `in_progress`,
        `done` or `blocked`. Call this once at the start of a task to set the
        plan, then update each item as you progress. The UI shows the roadmap
        next to the chat.

        Actions:
            set: Replace the whole roadmap with `items`.
            add: Append `items` to the roadmap.
            update: Change the item `item_id` using any of `title`, `status`
                or `notes`.
            remove: Delete the item `item_id`.
            clear: Remove all items.

        Args:
            action: Operation to perform.
            items: Tasks for `set` or `add`. Each item needs a title; status
                defaults to `pending`.
            item_id: Task id for `update` or `remove`.
            title: New title for `update`.
            status: New status for `update`.
            notes: New notes for `update`.
        """
        if action == 'set':
            if items is None:
                raise ValueError('`items` is required for action `set`.')
            store.set_items([_coerce(item) for item in items])
        elif action == 'add':
            if not items:
                raise ValueError('`items` is required for action `add`.')
            store.add_items([_coerce(item) for item in items])
        elif action == 'update':
            if not item_id:
                raise ValueError('`item_id` is required for action `update`.')
            try:
                store.update_item(item_id, title=title, status=status, notes=notes)
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
        elif action == 'remove':
            if not item_id:
                raise ValueError('`item_id` is required for action `remove`.')
            if not store.remove_item(item_id):
                raise ValueError(f'No roadmap item with id {item_id!r}.')
        elif action == 'clear':
            store.clear()
        else:
            raise ValueError(f'Unknown action: {action!r}.')
        return _snapshot(store)


__all__ = ['RoadmapItemInput', 'RoadmapSnapshot', 'register_roadmap_tools']
