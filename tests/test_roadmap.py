"""Tests for the roadmap store and tool.

The behaviours survive: set/add/update/remove/clear, persistence across instances,
per-execution isolation, listeners, and memory-only without a session. What changed is
that a plan belongs to an execution, so each test starts from a real session — which
is what the app does too.
"""

import pytest

from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.tools import RoadmapItemInput, register_roadmap_tools
from src.engine.sessions.session_store import SessionStore


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


def test_a_store_without_an_execution_is_memory_only(tmp_path):
    """Before a session exists there is nothing to key a plan by, so nothing is stored."""
    store = RoadmapStore(base_dir=tmp_path)
    assert store.execution_id is None
    store.set_items([TodoItem(title='A')])
    assert [item.title for item in store.snapshot()] == ['A']
    assert store.persistence_error is None
