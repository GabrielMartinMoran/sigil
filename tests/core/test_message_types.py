"""Types survive real enqueue delivery, serialization, continuation and storage."""

import uuid
from pathlib import Path

from src.core.agents.message_types import origin
from src.core.sessions.session_store import SessionStore
from src.core.storage.accepted_inputs import AcceptedInputs
from src.core.storage.database import Database, database_path


def _report() -> dict:
    return {
        'node_id': 'child',
        'label': 'Scout',
        'short_id': 'child',
        'name': 'scout',
        'status': 'failed',
        'text': 'failure details',
    }


def test_accepted_origin_is_persisted_and_default_is_user(tmp_path: Path) -> None:
    path = database_path(tmp_path)
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path)
    database = store.database
    try:
        session = store.create_session()
        report_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
        value = origin('subagent_report', reports=[_report()])
        store.accept_input(session, report_id, 'same', [], 'turn', origin=value)
        store.accept_input(session, user_id, 'same', [], 'turn')
        session_id = session.id
    finally:
        Database.forget(database.path)
    database = Database.at(path)
    try:
        rows = AcceptedInputs(database).list(session_id)
        assert rows[0].origin == value
        assert rows[1].origin is None
        assert rows[0].text == rows[1].text == 'same'
    finally:
        Database.forget(database.path)
