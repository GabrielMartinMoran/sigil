"""Maintenance: what keeps the database from growing without a ceiling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.engine.sessions.session_store import SessionStore
from src.engine.storage.blobs import ABANDONED, IMAGE
from src.engine.storage.database import Database, database_path
from src.engine.storage.maintenance import (
    backup,
    collect_blobs,
    drop_old_checkpoints,
    export_session,
    run,
)


def _turn(index: int) -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content=f'q{index}')], instructions='INSTR', run_id=f'r{index}'),
        ModelResponse(parts=[TextPart(content='a')], run_id=f'r{index}'),
    ]


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    base = tmp_path / 'home'
    Database.forget(database_path(base))
    return SessionStore(cwd=tmp_path / 'ws', base_dir=base)


def test_the_collector_removes_instructions_nothing_points_at(store: SessionStore):
    orphan = store.blobs.put_text('a skill nobody uses any more')
    assert collect_blobs(store.database, store.blobs) == 1
    assert store.database.value('SELECT count(*) FROM blobs WHERE id = ?', (orphan,)) == 0


def test_the_collector_leaves_images_alone(store: SessionStore):
    """An image is referenced from inside a payload, which the collector cannot see.

    Deleting it would remove pixels `view_image` still needs, so they are collected by
    age and by session instead.
    """
    image = store.blobs.put(b'\x89PNG', kind=IMAGE, media_type='image/png')

    collect_blobs(store.database, store.blobs)

    assert store.database.value('SELECT count(*) FROM blobs WHERE id = ?', (image,)) == 1


def test_the_collector_keeps_what_a_checkpoint_needs(store: SessionStore):
    """A rollback restores a plan from a blob and undoes itself from another."""
    session = store.create_session()
    session.messages = _turn(0)
    store.save(session)
    plan_blobs = store.database.value("SELECT count(*) FROM blobs WHERE kind = 'roadmap'")
    abandoned = store.blobs.put('{}', kind=ABANDONED, media_type='application/json')

    collect_blobs(store.database, store.blobs)

    assert store.database.value("SELECT count(*) FROM blobs WHERE kind = 'roadmap'") == plan_blobs
    assert store.database.value('SELECT count(*) FROM blobs WHERE id = ?', (abandoned,)) == 1


def test_a_session_can_be_exported_and_read(store: SessionStore):
    session = store.create_session()
    session.messages = _turn(0) + _turn(1)
    session.title = 'A session'
    store.save(session)

    document = json.loads(export_session(store.database, store.blobs, session.id))

    assert document['id'] == session.id
    assert document['title'] == 'A session'
    assert len(document['executions']) == 1
    root = document['executions'][0]
    assert len(root['messages']) == 4
    assert root['messages'][0]['instructions'] == 'INSTR'
    # The turn is a row in the export too, not something to reconstruct.
    assert len(root['turns']) == 2


def test_an_unknown_session_cannot_be_exported(store: SessionStore):
    with pytest.raises(KeyError):
        export_session(store.database, store.blobs, 'nope')


def test_a_backup_is_a_consistent_copy(store: SessionStore, tmp_path: Path):
    session = store.create_session()
    session.messages = _turn(0)
    store.save(session)

    target = backup(store.database, tmp_path / 'copy.db')

    copy = Database.at(target)
    assert copy.value('SELECT count(*) FROM sessions') == 1
    assert copy.value('SELECT count(*) FROM messages') == 2


def test_old_checkpoints_are_dropped_but_the_undo_is_kept(store: SessionStore):
    session = store.create_session()
    session.messages = [message for index in range(10) for message in _turn(index)]
    store.save(session)
    undo = store.database.value("SELECT count(*) FROM checkpoints WHERE reason = 'before_rollback'")

    dropped = drop_old_checkpoints(store.database, store.blobs, keep=3)

    assert dropped > 0
    assert store.database.value("SELECT count(*) FROM checkpoints WHERE reason = 'prompt'") == 3
    assert store.database.value("SELECT count(*) FROM checkpoints WHERE reason = 'before_rollback'") == undo


def test_a_pass_reports_what_it_did(store: SessionStore):
    store.blobs.put_text('nobody points here')
    session = store.create_session()
    session.messages = [message for index in range(8) for message in _turn(index)]
    store.save(session)

    report = run(store.database, store.blobs, keep_checkpoints=2)

    assert report.blobs_collected >= 1
    assert report.checkpoints_dropped >= 1
    assert report.bytes_after >= 0
    assert report.reclaimed >= 0


def test_maintenance_can_run_twice(store: SessionStore):
    """It runs on idle, so it has to be idempotent and cheap."""
    session = store.create_session()
    session.messages = _turn(0)
    store.save(session)

    first = run(store.database, store.blobs)
    second = run(store.database, store.blobs)

    assert second.blobs_collected == 0
    assert first.checkpoints_dropped == second.checkpoints_dropped == 0
