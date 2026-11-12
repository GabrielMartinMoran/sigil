"""Concurrent session refresh never overwrites a newer conversation."""

import sqlite3
import uuid
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from src.engine.sessions.session_store import SessionStore


def test_refresh_reads_external_changes_without_saving_stale_history(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    session = store.create_session()
    stale = store.load(session.id)
    assert stale is not None
    session.messages = [ModelRequest(parts=[UserPromptPart('external')])]
    store.save(session)

    refreshed = store.refresh(stale)

    assert refreshed is not None
    assert refreshed.messages == session.messages
    assert len(store.list_sessions()) == 1


def test_conflicting_progress_is_preserved_in_a_recovery_session(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    session = store.create_session()
    stale = store.load(session.id)
    assert stale is not None
    session.messages = [ModelRequest(parts=[UserPromptPart('external')])]
    store.save(session)
    stale.messages = [ModelRequest(parts=[UserPromptPart('local')])]

    recovery_id = store.save_progress(stale)
    stale.messages.append(ModelRequest(parts=[UserPromptPart('more local')]))
    assert store.save_progress(stale) == recovery_id

    latest = store.load(session.id)
    recovery = store.load(recovery_id)
    assert latest is not None and latest.messages == session.messages
    assert recovery is not None and recovery.messages == stale.messages
    assert len(store.list_sessions()) == 2
    refreshed = store.save_current(stale)
    assert refreshed.messages == session.messages
    assert len(store.list_sessions()) == 2


def test_save_current_reloads_a_stale_session_instead_of_raising(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    session = store.create_session()
    stale = store.load(session.id)
    assert stale is not None
    session.title = 'External title'
    store.save(session)

    current = store.save_current(stale)

    assert current.title == 'External title'
    assert current.id == stale.id
    assert len(store.list_sessions()) == 1


def test_recovery_preserves_subagents_and_does_not_change_default_session(tmp_path: Path) -> None:
    from src.engine.agents.profile.models.agent_role import AgentRole
    from src.engine.agents.tree.store import AgentTreeStore

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    session = store.create_session()
    tree = AgentTreeStore().bind(session, root_name='Wizard')
    store.save(session)
    stale = store.load(session.id)
    assert stale is not None
    session.messages = [ModelRequest(parts=[UserPromptPart('external')])]
    store.save(session)
    stale.tree.root.messages = [ModelRequest(parts=[UserPromptPart('root snapshot')])]
    child = stale.tree.add(parent_id=stale.tree.root_id, name='scout', role=AgentRole.AGENT)
    child.messages = [ModelRequest(parts=[UserPromptPart('child work')])]
    stale.tree.notifications.publish(child, stale.tree.root_id, 'child report')
    stale.messages = [ModelRequest(parts=[UserPromptPart('local')])]

    recovery_id = store.save_progress(stale)
    assert store.load_last().id == session.id
    recovery = store.load(recovery_id)

    assert recovery is not None
    assert recovery.tree.root.id == recovery.root_id
    assert recovery.tree.root.id != tree.root.id
    assert recovery.messages == stale.messages
    restored_child = recovery.tree.children(None)[0]
    assert restored_child.id != child.id
    assert restored_child.messages == child.messages
    assert restored_child.status.finished
    notice = recovery.tree.notifications.all()[0]
    assert notice.sender_id == restored_child.id
    assert notice.receiver_id == recovery.root_id


def test_a_recovery_checkpoint_links_and_delivers_in_one_transaction(tmp_path: Path) -> None:
    from src.engine.storage.accepted_inputs import AcceptedInputs, DeliveryConfirmation
    from src.engine.storage.database import Database, database_path

    base = tmp_path / 'state'
    store = SessionStore(cwd=tmp_path, base_dir=base)
    session = store.create_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'steering', [], 'steer')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    stale = store.load(session.id)
    assert stale is not None
    session.messages = [ModelRequest(parts=[UserPromptPart('external')])]
    store.save(session)
    stale.messages = [ModelRequest(parts=[UserPromptPart('local progress')])]

    result = store.save_progress_result(
        stale,
        confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'run')],
    )

    assert result is not None
    assert result.session_id == session.id
    recovery_id = result.recovery_session_id
    assert recovery_id is not None and recovery_id != session.id
    Database.forget(database_path(base))
    reopened = SessionStore(cwd=tmp_path, base_dir=base)
    recovery = reopened.load(recovery_id)
    assert recovery is not None and recovery.messages == stale.messages
    link = reopened.database.one(
        'SELECT destination_id, destination_session_id FROM recovery_links WHERE original_session_id = ?',
        (session.id,),
    )
    assert tuple(link) == (recovery_id, recovery_id)
    fact = reopened.database.one(
        'SELECT checkpointed, destination_id, destination_session_id FROM input_deliveries '
        'WHERE session_id = ? AND request_id = ?',
        (session.id, request),
    )
    assert tuple(fact) == (1, recovery_id, recovery_id)
    assert AcceptedInputs(reopened.database).list(session.id)[0].state == 'delivered'
    assert reopened.load(session.id).messages == session.messages


def test_a_failed_recovery_delivery_rolls_back_the_recovery_copy(tmp_path: Path) -> None:
    from src.engine.storage.accepted_inputs import AcceptedInputs, DeliveryConfirmation

    base = tmp_path / 'state'
    store = SessionStore(cwd=tmp_path, base_dir=base)
    session = store.create_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'steering', [], 'steer')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    stale = store.load(session.id)
    assert stale is not None
    session.messages = [ModelRequest(parts=[UserPromptPart('external')])]
    store.save(session)
    stale.messages = [ModelRequest(parts=[UserPromptPart('local progress')])]
    before = {summary.id for summary in store.list_sessions()}
    store.database.execute(
        'CREATE TRIGGER fail_recovery_delivery BEFORE INSERT ON input_deliveries '
        "BEGIN SELECT RAISE(ABORT, 'failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.save_progress_result(
            stale,
            confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'run')],
        )

    assert {summary.id for summary in store.list_sessions()} == before
    assert store.database.value('SELECT count(*) FROM recovery_links') == 0
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0
    assert queue.list(session.id)[0].state == 'claimed'


def test_characterize_current_recovery_missing_root_and_child_plans(tmp_path: Path) -> None:
    """Missing recovery plans are a defect, not the contract; update assertions when fixed."""
    from src.engine.agents.profile.models.agent_role import AgentRole
    from src.engine.agents.tree.store import AgentTreeStore
    from src.engine.roadmap.models.todo_item import TodoItem
    from src.engine.roadmap.store import RoadmapStore
    from src.engine.storage.database import Database, database_path

    base = tmp_path / 'state'
    store = SessionStore(cwd=tmp_path, base_dir=base)
    session = store.create_session()
    tree = AgentTreeStore().bind(session, root_name='Wizard')
    child = tree.add(parent_id=tree.root_id, name='scout', role=AgentRole.AGENT)
    store.save(session)
    root_plan = RoadmapStore(session.root_id, base_dir=base)
    child_plan = RoadmapStore(child.id, base_dir=base)
    root_plan.set_items([TodoItem(title='root baseline')])
    child_plan.set_items([TodoItem(title='child baseline')])
    stale = store.load(session.id)
    assert stale is not None
    session.title = 'winning session'
    store.save(session)
    root_plan.add_items([TodoItem(title='root local')])
    child_plan.add_items([TodoItem(title='child local')])
    stale.messages = [ModelRequest(parts=[UserPromptPart('local progress')])]

    recovery_id = store.save_progress(stale)
    assert recovery_id is not None and recovery_id != session.id
    assert root_plan.persistence_error is None
    assert child_plan.persistence_error is None
    Database.forget(database_path(base))
    reopened = SessionStore(cwd=tmp_path, base_dir=base)
    winner = reopened.load(session.id)
    recovery = reopened.load(recovery_id)
    assert winner is not None and winner.title == 'winning session'
    assert recovery is not None and recovery.messages == stale.messages
    restored_child = recovery.tree.children(None)[0]
    assert restored_child.id != child.id
    assert [item.title for item in RoadmapStore(session.root_id, base_dir=base).snapshot()] == [
        'root baseline',
        'root local',
    ]
    assert [item.title for item in RoadmapStore(child.id, base_dir=base).snapshot()] == [
        'child baseline',
        'child local',
    ]
    assert RoadmapStore(recovery.root_id, base_dir=base).snapshot() == []
    assert RoadmapStore(restored_child.id, base_dir=base).snapshot() == []
    Database.forget(database_path(base))


def test_characterize_current_roadmap_only_conflict_leaks_to_winner(tmp_path: Path) -> None:
    """Writing the local plan to the winner is a defect, not the contract; update assertions when fixed."""
    from src.engine.roadmap.models.todo_item import TodoItem
    from src.engine.roadmap.store import RoadmapStore
    from src.engine.storage.database import Database, database_path

    base = tmp_path / 'state'
    store = SessionStore(cwd=tmp_path, base_dir=base)
    session = store.create_session()
    plan = RoadmapStore(session.root_id, base_dir=base)
    plan.set_items([TodoItem(title='baseline')])
    stale = store.load(session.id)
    assert stale is not None
    session.title = 'winning session'
    store.save(session)
    plan.add_items([TodoItem(title='local roadmap only')])

    recovery_id = store.save_progress(stale)
    assert recovery_id is None
    assert plan.persistence_error is None
    Database.forget(database_path(base))
    reopened = SessionStore(cwd=tmp_path, base_dir=base)
    winner = reopened.load(session.id)
    assert winner is not None and winner.title == 'winning session'
    assert len(reopened.list_sessions()) == 1
    assert [item.title for item in RoadmapStore(session.root_id, base_dir=base).snapshot()] == [
        'baseline',
        'local roadmap only',
    ]
    Database.forget(database_path(base))


def test_refresh_observes_changes_from_a_separate_process(tmp_path: Path) -> None:
    import subprocess
    import sys

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    session = store.create_session()
    script = """
import sys
from src.engine.sessions.session_store import SessionStore
store = SessionStore(cwd=sys.argv[1], base_dir=sys.argv[2])
session = store.load(sys.argv[3])
session.title = 'written by another process'
store.save(session)
"""
    subprocess.run(
        [sys.executable, '-c', script, str(tmp_path), str(tmp_path / 'state'), session.id],
        check=True,
        capture_output=True,
        timeout=15,
        cwd=Path(__file__).resolve().parents[1],
    )

    current = store.save_current(session)

    assert current.title == 'written by another process'
    assert len(store.list_sessions()) == 1


def test_progress_keeps_both_copies_when_the_recovery_session_is_edited_elsewhere(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    session = store.create_session()
    stale = store.load(session.id)
    assert stale is not None
    session.title = 'external update'
    store.save(session)
    stale.messages = [ModelRequest(parts=[UserPromptPart('local progress')])]
    first_recovery_id = store.save_progress(stale)
    edited = store.load(first_recovery_id)
    assert edited is not None
    edited.messages.append(ModelRequest(parts=[UserPromptPart('recovery edited elsewhere')]))
    store.save(edited)
    stale.messages.append(ModelRequest(parts=[UserPromptPart('continued local progress')]))

    second_recovery_id = store.save_progress(stale)

    assert second_recovery_id != first_recovery_id
    assert store.load(first_recovery_id).messages == edited.messages
    assert store.load(second_recovery_id).messages == stale.messages
    assert store.load(session.id).title == 'external update'
