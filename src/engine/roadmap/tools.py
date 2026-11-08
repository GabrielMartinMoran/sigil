"""`update_roadmap` tool: plan of the current task, visible in the sidebar."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.models.todo_status import TodoStatus
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.stores import RoadmapStores
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.lod_policies import UPDATE_ROADMAP_LOD
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ToolGroup, ToolPermission
from src.engine.tooling.registrar import ToolRegistrar

_ROADMAP = ToolPermission(groups=(ToolGroup.ROADMAP,))


class RoadmapItemInput(BaseModel):
    """An input task for the roadmap."""

    title: str = Field(min_length=1)
    id: str | None = None
    status: TodoStatus = 'pending'
    notes: str | None = None


class RoadmapSnapshot(BaseModel):
    """Full roadmap state."""

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


def _apply_set(store: RoadmapStore, items: list[RoadmapItemInput] | None) -> None:
    if items is None:
        raise ValueError('`items` is required for action `set`.')
    store.set_items([_coerce(item) for item in items])


def _apply_add(store: RoadmapStore, items: list[RoadmapItemInput] | None) -> None:
    if not items:
        raise ValueError('`items` is required for action `add`.')
    store.add_items([_coerce(item) for item in items])


def _apply_update(
    store: RoadmapStore,
    item_id: str | None,
    title: str | None,
    status: TodoStatus | None,
    notes: str | None,
) -> None:
    if not item_id:
        raise ValueError('`item_id` is required for action `update`.')
    try:
        store.update_item(item_id, title=title, status=status, notes=notes)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc


def _apply_remove(store: RoadmapStore, item_id: str | None) -> None:
    if not item_id:
        raise ValueError('`item_id` is required for action `remove`.')
    if not store.remove_item(item_id):
        raise ValueError(f'No roadmap item with id {item_id!r}.')


def register_roadmap_tools(
    agent: Any,
    store: RoadmapStore | RoadmapStores,
    logger: ToolLogger | None = None,
    catalog: ToolCatalog | None = None,
) -> None:
    """Registers the roadmap tool on a pydantic-ai agent.

    With a ``RoadmapStores`` the store is resolved per run: a subagent plans in its
    own roadmap and not in its parent's, because the two plans answer different
    questions.
    """
    tools = ToolRegistrar(agent, logger, catalog)

    def store_for(ctx: Any) -> RoadmapStore:
        """The roadmap of the node this run belongs to."""
        if isinstance(store, RoadmapStores):
            deps = getattr(ctx, 'deps', None)
            return store.for_node(getattr(deps, 'node_id', None))
        return store

    @tools.context_tool(permission=_ROADMAP, lod=UPDATE_ROADMAP_LOD)
    def update_roadmap(
        ctx: RunContext[Any],
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
        store = store_for(ctx)
        if action == 'set':
            _apply_set(store, items)
        elif action == 'add':
            _apply_add(store, items)
        elif action == 'update':
            _apply_update(store, item_id, title, status, notes)
        elif action == 'remove':
            _apply_remove(store, item_id)
        elif action == 'clear':
            store.clear()
        else:
            raise ValueError(f'Unknown action: {action!r}.')
        return _snapshot(store)
