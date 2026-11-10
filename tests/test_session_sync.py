"""Concurrent session refresh never overwrites a newer conversation."""

from pathlib import Path

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
