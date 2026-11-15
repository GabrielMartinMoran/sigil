"""Tests for the roadmap store and tool.

The behaviours survive: set/add/update/remove/clear, persistence across instances,
per-execution isolation, listeners, and memory-only without a session. What changed is
that a plan belongs to an execution, so each test starts from a real session — which
is what the app does too.
"""

import json
from pathlib import Path

import pytest

from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.tools import RoadmapItemInput, register_roadmap_tools
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.roadmap import (
    RoadmapConflictError,
    read_revision,
    replace_items,
    restore_plan_from_json,
)


def _session(tmp_path):
    """A stored session: a plan belongs to an execution, and an execution to one."""
    return SessionStore(cwd=tmp_path / 'ws', base_dir=tmp_path).create_session()


def _store(tmp_path):
    return RoadmapStore(_session(tmp_path).root_id, base_dir=tmp_path)


class DummyAgent:
    def __init__(self) -> None:
        self.tools = {}

    def _register(self, func, kwargs):
        name = kwargs.get('name') or func.__name__
        self.tools[name] = func
        return func

    def tool_plain(self, func, **kwargs):
        return self._register(func, kwargs)

    def tool(self, func, **kwargs):
        return self._register(func, kwargs)


class _Ctx:
    """The minimum of a `RunContext` the roadmap tool reads: the node it belongs to."""

    deps = None


def _tool(store):
    agent = DummyAgent()
    register_roadmap_tools(agent, store)
    raw = agent.tools['update_roadmap']

    def call(**kwargs):
        return raw(_Ctx(), **kwargs)

    return call


def test_set_add_update_remove_clear(tmp_path):
    store = _store(tmp_path)
    tool = _tool(store)

    snap = tool(action='set', items=[RoadmapItemInput(title='A'), RoadmapItemInput(title='B', status='in_progress')])
    assert len(snap.items) == 2
    assert snap.counts['pending'] == 1
    assert snap.counts['in_progress'] == 1

    snap = tool(action='add', items=[RoadmapItemInput(title='C')])
    assert len(snap.items) == 3

    item_id = snap.items[0].id
    snap = tool(action='update', item_id=item_id, status='done', notes='ok')
    updated = next(item for item in snap.items if item.id == item_id)
    assert updated.status == 'done'
    assert updated.notes == 'ok'

    snap = tool(action='remove', item_id=item_id)
    assert all(item.id != item_id for item in snap.items)

    snap = tool(action='clear')
    assert snap.items == []


def test_persistence_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.set_items([TodoItem(title='A', status='done')])
    assert store.persistence_error is None

    # A second store reading the same execution sees the plan: it is rows, not memory.
    reloaded = RoadmapStore(store.execution_id, base_dir=tmp_path)
    items = reloaded.snapshot()
    assert len(items) == 1
    assert items[0].title == 'A'
    assert items[0].status == 'done'


def test_the_order_of_the_plan_is_kept(tmp_path):
    store = _store(tmp_path)
    store.set_items([TodoItem(title=title) for title in ('first', 'second', 'third')])

    reloaded = RoadmapStore(store.execution_id, base_dir=tmp_path)
    assert [item.title for item in reloaded.snapshot()] == ['first', 'second', 'third']


def test_update_unknown_id_raises(tmp_path):
    store = _store(tmp_path)
    tool = _tool(store)
    with pytest.raises(ValueError, match='No roadmap item'):
        tool(action='update', item_id='nope', status='done')


def test_listener_is_notified(tmp_path):
    store = _store(tmp_path)
    calls = []
    store.add_listener(lambda: calls.append(1))
    store.set_items([TodoItem(title='A')])
    assert calls == [1]


def test_each_execution_has_its_own_plan(tmp_path):
    """A plan belongs to the execution that is executing it, not to the session."""
    session = _session(tmp_path)
    first = RoadmapStore(session.root_id, base_dir=tmp_path)
    second = RoadmapStore('a' * 32, base_dir=tmp_path, flush=lambda: session)
    tool = _tool(first)
    tool(action='set', items=[RoadmapItemInput(title='From the root')])

    assert [item.title for item in first.snapshot()] == ['From the root']
    assert second.snapshot() == []


def test_binding_moves_the_store_to_another_execution(tmp_path):
    session = _session(tmp_path)
    other = _session(tmp_path)
    store = RoadmapStore(session.root_id, base_dir=tmp_path)
    tool = _tool(store)
    tool(action='set', items=[RoadmapItemInput(title='From the first')])

    store.bind(other.root_id)
    assert store.snapshot() == []
    tool(action='set', items=[RoadmapItemInput(title='From the second')])

    store.bind(session.root_id)
    assert [item.title for item in store.snapshot()] == ['From the first']

    store.bind(other.root_id)
    assert [item.title for item in store.snapshot()] == ['From the second']


def test_bind_notifies_listeners(tmp_path):
    session = _session(tmp_path)
    store = RoadmapStore(session.root_id, base_dir=tmp_path)
    calls = []
    store.add_listener(lambda: calls.append(1))
    store.bind('a' * 32)
    assert calls == [1]
    # Binding to the same execution is a no-op.
    store.bind('a' * 32)
    assert calls == [1]


def test_stale_store_preserves_dirty_items_and_reports_conflict(tmp_path: Path) -> None:
    first = _store(tmp_path)
    first.set_items([TodoItem(title='baseline')])
    second = RoadmapStore(first.execution_id, base_dir=tmp_path)
    assert [item.title for item in first.snapshot()] == ['baseline']
    assert [item.title for item in second.snapshot()] == ['baseline']

    first.add_items([TodoItem(title='first update')])
    after_first = RoadmapStore(first.execution_id, base_dir=tmp_path)
    assert [item.title for item in after_first.snapshot()] == ['baseline', 'first update']
    second.add_items([TodoItem(title='second update')])

    reopened = RoadmapStore(first.execution_id, base_dir=tmp_path)
    assert first.persistence_error is None
    assert second.persistence_error is not None
    assert 'conflict' in second.persistence_error.lower()
    assert [item.title for item in first.snapshot()] == ['baseline', 'first update']
    assert [item.title for item in second.snapshot()] == ['baseline', 'second update']
    assert [item.title for item in reopened.snapshot()] == ['baseline', 'first update']


def test_a_conflict_does_not_move_the_stored_revision(tmp_path):
    """A refused write leaves the rows and the stored revision exactly as they were."""
    store = _store(tmp_path)
    store.set_items([TodoItem(title='A')])
    before = read_revision(store.database, store.execution_id)

    with pytest.raises(RoadmapConflictError):
        replace_items(
            store.database,
            store.execution_id,
            [TodoItem(title='B')],
            expected_revision=before - 1,
        )

    assert read_revision(store.database, store.execution_id) == before
    assert [item.title for item in store.snapshot()] == ['A']


def test_clear_keeps_the_revision_moving_forward(tmp_path):
    """An empty plan is still a plan at a revision, not a plan that never moved."""
    store = _store(tmp_path)
    store.set_items([TodoItem(title='A')])
    store.clear()
    assert store.persistence_error is None

    execution = store.execution_id
    revision = store.database.value('SELECT revision FROM roadmap_revisions WHERE execution_id = ?', (execution,))
    assert revision == 2

    reopened = RoadmapStore(execution, base_dir=tmp_path)
    assert reopened.snapshot() == []
    reopened.add_items([TodoItem(title='B')])
    assert reopened.persistence_error is None
    assert reopened.database.value('SELECT revision FROM roadmap_revisions WHERE execution_id = ?', (execution,)) == 3


def test_forget_empties_the_plan_without_resetting_the_revision(tmp_path):
    store = _store(tmp_path)
    store.set_items([TodoItem(title='A')])
    store.forget()
    assert store.persistence_error is None
    assert store.snapshot() == []

    execution = store.execution_id
    assert store.database.value('SELECT revision FROM roadmap_revisions WHERE execution_id = ?', (execution,)) == 2
    assert RoadmapStore(execution, base_dir=tmp_path).snapshot() == []


def test_restoring_a_plan_moves_the_revision_forward(tmp_path):
    store = _store(tmp_path)
    store.set_items([TodoItem(title='A')])
    raw = json.dumps([TodoItem(title='B').model_dump()])

    assert restore_plan_from_json(store.database, store.execution_id, raw) == 1
    assert [item.title for item in store.snapshot()] == ['A']

    reopened = RoadmapStore(store.execution_id, base_dir=tmp_path)
    assert [item.title for item in reopened.snapshot()] == ['B']
    assert (
        reopened.database.value(
            'SELECT revision FROM roadmap_revisions WHERE execution_id = ?',
            (store.execution_id,),
        )
        == 2
    )


def test_a_store_without_an_execution_is_memory_only(tmp_path):
    """Before a session exists there is nothing to key a plan by, so nothing is stored."""
    store = RoadmapStore(base_dir=tmp_path)
    assert store.execution_id is None
    store.set_items([TodoItem(title='A')])
    assert [item.title for item in store.snapshot()] == ['A']
    assert store.persistence_error is None
