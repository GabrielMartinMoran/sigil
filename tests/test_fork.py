"""Fork: the same instant, without destroying anything."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.engine.agents.rollback import Rollback, RollbackError
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.database import Database, database_path


def _turn(index: int) -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content=f'q{index}')], instructions='I', run_id=f'r{index}'),
        ModelResponse(parts=[TextPart(content=f'a{index}')], run_id=f'r{index}'),
    ]


def _session(tmp_path):
    base = tmp_path / 'home'
    Database.forget(database_path(base))
    store = SessionStore(cwd=tmp_path / 'ws', base_dir=base)
    return store, store.create_session()


def _points(store, session):
    return Rollback(store, workspace=tmp_path_of(store)).points(session.id)


def tmp_path_of(store: SessionStore):
    return store.cwd


def test_a_fork_copies_the_prefix_and_leaves_the_original(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1) + _turn(2)
    store.save(session)
    original = len(store.load(session.id).messages)
    points = Rollback(store, workspace=store.cwd).points(session.id)

    outcome = Rollback(store, workspace=store.cwd).fork_to(session.id, points[1].turn_id)

    # The original is untouched.
    assert len(store.load(session.id).messages) == original
    # `points` is newest first, so `points[1]` is the second turn: the fork keeps the
    # first one and nothing of the turn it branched at.
    forked = store.load(outcome.session_id)
    assert forked is not None
    assert len(forked.messages) == 2
    assert 'q0' in str(forked.messages[0].parts[0].content)
    assert outcome.turns_copied == 1


def test_a_fork_is_a_session_of_its_own(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    session.title = 'the original'
    store.save(session)
    points = Rollback(store, workspace=store.cwd).points(session.id)

    outcome = Rollback(store, workspace=store.cwd).fork_to(session.id, points[0].turn_id)

    listed = {summary.id: summary for summary in store.list_sessions()}
    assert outcome.session_id in listed
    assert session.id in listed
    assert listed[outcome.session_id].display_title == 'the original (fork)'
    # Two sessions, two roots, and the fork's root id is derived from its own id.
    forked = store.load(outcome.session_id)
    assert forked.root_id != session.root_id
    assert forked.root_id == forked.tree.root.id


def test_the_fork_keeps_the_plan_of_the_instant_it_branched(tmp_path):
    store, session = _session(tmp_path)
    plans = RoadmapStores(session_id=session.id, base_dir=tmp_path / 'home')
    session.messages = _turn(0)
    plans.root.set_items([TodoItem(title='step one')])
    store.save(session)
    session.messages += _turn(1)
    store.save(session)
    plans.root.add_items([TodoItem(title='step two')])
    store.save(session)

    points = Rollback(store, workspace=store.cwd).points(session.id)
    outcome = Rollback(
        store,
        workspace=store.cwd,
        on_plan_changed=plans.reload_all,
    ).fork_to(session.id, points[0].turn_id)

    forked_plans = RoadmapStores(session_id=outcome.session_id, base_dir=tmp_path / 'home')
    assert [item.title for item in forked_plans.root.snapshot()] == ['step one']
    # And the original keeps its own, both items.
    assert [item.title for item in plans.root.snapshot()] == ['step one', 'step two']


def test_a_fork_does_not_touch_the_workspace(tmp_path):
    """Nothing is being undone, so there is nothing to rewind. That is the difference."""
    store, session = _session(tmp_path)
    workspace = tmp_path / 'ws'
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / 'file.txt').write_text('written during the turn')
    session.messages = _turn(0) + _turn(1)
    store.save(session)
    points = Rollback(store, workspace=workspace).points(session.id)

    Rollback(store, workspace=workspace).fork_to(session.id, points[0].turn_id)

    assert (workspace / 'file.txt').read_text() == 'written during the turn'


def test_the_fork_shares_the_blobs_of_the_original(tmp_path):
    """Content addressing means a fork does not duplicate a single instruction block."""
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    store.save(session)
    before = store.database.value("SELECT count(*) FROM blobs WHERE kind = 'instructions'")
    points = Rollback(store, workspace=store.cwd).points(session.id)

    Rollback(store, workspace=store.cwd).fork_to(session.id, points[1].turn_id)

    after = store.database.value("SELECT count(*) FROM blobs WHERE kind = 'instructions'")
    assert after == before


def test_forking_a_turn_that_does_not_exist_is_refused(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0)
    store.save(session)

    with pytest.raises(RollbackError, match='No turn'):
        Rollback(store, workspace=store.cwd).fork_to(session.id, 'nope')


def test_the_fork_can_be_rolled_back_and_written_to(tmp_path):
    """The copy is independent: writing to one must not move the other."""
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    store.save(session)
    points = Rollback(store, workspace=store.cwd).points(session.id)
    # `points[0]` is the newest turn: forking there keeps the first one intact.
    outcome = Rollback(store, workspace=store.cwd).fork_to(session.id, points[0].turn_id)
    assert len(store.load(outcome.session_id).messages) == 2

    forked = store.load(outcome.session_id)
    forked.messages += _turn(3)
    store.save(forked)

    # The original still has its two turns; the fork has the prefix plus its own.
    assert len(store.load(session.id).messages) == 4
    assert len(store.load(outcome.session_id).messages) == 4
