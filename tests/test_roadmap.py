"""Tests del store y la tool de roadmap."""

import pytest

from src.engine.roadmap.store import RoadmapStore, TodoItem
from src.engine.tools.roadmap import RoadmapItemInput, register_roadmap_tools


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


def _tool(store):
    agent = DummyAgent()
    register_roadmap_tools(agent, store)
    return agent.tools['update_roadmap']


def test_set_add_update_remove_clear(tmp_path):
    store = RoadmapStore(directory=tmp_path, session_id='s1')
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
    store = RoadmapStore(directory=tmp_path, session_id='s1')
    store.set_items([TodoItem(title='A', status='done')])
    assert store.path is not None and store.path.exists()

    reloaded = RoadmapStore(directory=tmp_path, session_id='s1')
    items = reloaded.snapshot()
    assert len(items) == 1
    assert items[0].title == 'A'
    assert items[0].status == 'done'


def test_update_unknown_id_raises(tmp_path):
    store = RoadmapStore(directory=tmp_path, session_id='s1')
    tool = _tool(store)
    with pytest.raises(ValueError, match='No roadmap item'):
        tool(action='update', item_id='nope', status='done')


def test_listener_is_notified(tmp_path):
    store = RoadmapStore(directory=tmp_path, session_id='s1')
    calls = []
    store.add_listener(lambda: calls.append(1))
    store.set_items([TodoItem(title='A')])
    assert calls == [1]


def test_each_session_has_its_own_roadmap(tmp_path):
    store = RoadmapStore(directory=tmp_path, session_id='s1')
    tool = _tool(store)
    tool(action='set', items=[RoadmapItemInput(title='De la sesión 1')])

    store.bind_session('s2')
    assert store.snapshot() == []
    tool(action='set', items=[RoadmapItemInput(title='De la sesión 2')])

    store.bind_session('s1')
    assert [item.title for item in store.snapshot()] == ['De la sesión 1']

    store.bind_session('s2')
    assert [item.title for item in store.snapshot()] == ['De la sesión 2']


def test_bind_session_notifies_listeners(tmp_path):
    store = RoadmapStore(directory=tmp_path, session_id='s1')
    calls = []
    store.add_listener(lambda: calls.append(1))
    store.bind_session('s2')
    assert calls == [1]
    # Rebinding to the same session is a no-op.
    store.bind_session('s2')
    assert calls == [1]


def test_store_without_session_is_memory_only(tmp_path):
    store = RoadmapStore(directory=tmp_path)
    assert store.path is None
    store.set_items([TodoItem(title='A')])
    assert [item.title for item in store.snapshot()] == ['A']
    assert list(tmp_path.iterdir()) == []
