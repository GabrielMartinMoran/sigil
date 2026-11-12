"""The SQLite schema upgrade is transactional and serialized across processes."""

import multiprocessing
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.engine.storage.database import Database
from src.engine.storage.schema import USER_VERSION


def test_v7_structures_preserve_facts_and_deletions(tmp_path: Path) -> None:
    from src.engine.sessions.session_store import SessionStore

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    original = store.create_session()
    first = store.create_session()
    second = store.create_session()
    db = store.database
    assert db.value('PRAGMA user_version') == 7
    execution = db.value('SELECT id FROM agent_executions WHERE session_id = ?', (original.id,))
    db.execute('INSERT INTO roadmap_revisions VALUES (?, ?)', (execution, 3))
    assert db.value('SELECT count(*) FROM roadmap_items WHERE execution_id = ?', (execution,)) == 0
    db.execute(
        "INSERT INTO accepted_inputs VALUES (?, 'request', 1, '{}', 'hash', 'turn', 'stopped', NULL, NULL, 'now', 'now')",
        (original.id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('INSERT INTO input_deliveries VALUES (?, ?, ?, ?, ?)', (original.id, 'request', 1, None, None))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('INSERT INTO recovery_links VALUES (?, ?, ?)', (original.id, first.id, second.id))
    db.execute('INSERT INTO input_deliveries VALUES (?, ?, ?, ?, ?)', (original.id, 'request', 1, first.id, first.id))
    for destination in (first.id, second.id):
        db.execute('INSERT INTO recovery_links VALUES (?, ?, ?)', (original.id, destination, destination))
    db.execute('DELETE FROM sessions WHERE id = ?', (first.id,))
    assert tuple(db.one('SELECT checkpointed, destination_id, destination_session_id FROM input_deliveries')) == (
        1,
        first.id,
        None,
    )
    assert tuple(
        db.one(
            'SELECT destination_id, destination_session_id FROM recovery_links WHERE destination_id = ?', (first.id,)
        )
    ) == (first.id, None)
    assert db.value('SELECT count(*) FROM recovery_links') == 2
    assert db.value('SELECT revision FROM roadmap_revisions WHERE execution_id = ?', (execution,)) == 3
    reopened = Database(db.path)
    try:
        assert reopened.value('SELECT revision FROM roadmap_revisions WHERE execution_id = ?', (execution,)) == 3
        assert reopened.value('SELECT count(*) FROM recovery_links') == 2
        assert reopened.value('SELECT destination_id FROM input_deliveries') == first.id
    finally:
        reopened.close()
    assert db.query('PRAGMA foreign_key_check') == []
    db.execute('DELETE FROM sessions WHERE id = ?', (original.id,))
    assert db.value('SELECT count(*) FROM recovery_links') == 0
    assert db.value('SELECT count(*) FROM input_deliveries') == 0
    assert db.value('SELECT count(*) FROM roadmap_revisions') == 0
    Database.forget(db.path)


def test_v6_delivery_is_unknown_after_upgrade(tmp_path: Path) -> None:
    from src.engine.sessions.session_store import SessionStore

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    db = store.database
    db.execute(
        "INSERT INTO accepted_inputs VALUES (?, 'request', 1, '{}', 'hash', 'turn', 'delivered', NULL, NULL, 'now', 'now')",
        (session.id,),
    )
    path = db.path
    for table in ('input_deliveries', 'recovery_links', 'roadmap_revisions'):
        db.execute(f'DROP TABLE {table}')
    db.execute('PRAGMA user_version = 6')
    Database.forget(path)
    db = Database(path)
    assert db.value('PRAGMA user_version') == 7
    assert db.value('SELECT count(*) FROM accepted_inputs') == 1
    assert db.value('SELECT count(*) FROM input_deliveries') == 0
    db.close()


def test_v7_failure_rolls_back_all_structures_and_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.storage import database as module

    path, session_id = _legacy_with_history(tmp_path)
    before = _snapshot(path)
    migrations = dict(module.MIGRATIONS)
    migrations[7] += '\nINVALID SQL;'
    with monkeypatch.context() as patch:
        patch.setattr(module, 'MIGRATIONS', migrations)
        with pytest.raises(sqlite3.OperationalError):
            Database(path)
    assert _snapshot(path) == before
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 5
        assert conn.execute('SELECT title FROM sessions WHERE id = ?', (session_id,)).fetchone() == ('preserved title',)
    db = Database(path)
    assert db.value('PRAGMA user_version') == 7
    assert db.query('PRAGMA foreign_key_check') == []
    db.close()


def _open(path: Path, results: Any) -> None:
    database = Database(path)
    results.put(database.value('PRAGMA user_version'))
    database.close()


def test_full_sync_and_future_version_are_safe(tmp_path: Path) -> None:
    path = tmp_path / 'db'
    db = Database(path)
    assert db.value('PRAGMA journal_mode') == 'wal'
    assert db.value('PRAGMA synchronous') == 2
    db.execute(f'PRAGMA user_version = {USER_VERSION + 1}')
    db.close()
    with pytest.raises(Exception, match='(?i)(newer|unsupported|incompatible)'):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == USER_VERSION + 1


def test_concurrent_processes_migrate_once(tmp_path: Path) -> None:
    path = tmp_path / 'db'
    db = Database(path)
    db.execute('DROP TABLE accepted_inputs')
    db.execute('PRAGMA user_version = 5')
    db.close()
    ctx = multiprocessing.get_context('spawn')
    results = ctx.Queue()
    processes = [ctx.Process(target=_open, args=(path, results)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert [results.get(timeout=2) for _ in processes] == [USER_VERSION, USER_VERSION]
    results.close()


def test_failed_migration_rolls_back_and_can_be_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.storage import database as module

    path = tmp_path / 'db'
    db = Database(path)
    db.execute('DROP TABLE accepted_inputs')
    db.execute("INSERT INTO app_state VALUES ('preserved', 'yes')")
    db.execute('PRAGMA user_version = 5')
    db.close()
    migrations = dict(module.MIGRATIONS)
    migrations[6] = 'CREATE TABLE injected (value TEXT); INVALID SQL;'
    with monkeypatch.context() as patch:
        patch.setattr(module, 'MIGRATIONS', migrations)
        with pytest.raises(sqlite3.OperationalError):
            Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 5
        assert connection.execute("SELECT value FROM app_state WHERE key = 'preserved'").fetchone() == ('yes',)
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE name = 'injected'").fetchone()[0] == 0
    reopened = Database(path)
    assert reopened.value('PRAGMA user_version') == USER_VERSION
    reopened.close()


def _accept_in_process(base: Path, session_id: str, request_id: str, ready: Any, start: Any, results: Any) -> None:
    from src.engine.storage.accepted_inputs import ImageInput, accept

    db = Database(base)
    ready.put(True)
    if not start.wait(10):
        raise TimeoutError('concurrent acceptance did not start')
    with db.transaction() as conn:
        accepted = accept(conn, session_id, request_id, 'same', [ImageInput(b'image', 'image/png')], 'turn')
    results.put((accepted.sequence, accepted.images[0].image_id))
    db.close()


def test_concurrent_process_retries_share_one_record(tmp_path: Path) -> None:
    import uuid

    from src.engine.sessions.session_store import SessionStore

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    context = multiprocessing.get_context('spawn')
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    request = str(uuid.uuid4())
    processes = [
        context.Process(
            target=_accept_in_process, args=(store.database.path, session.id, request, ready, start, results)
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=10)
        start.set()
        accepted = [results.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert accepted[0] == accepted[1]
        assert store.database.value('SELECT COUNT(*) FROM accepted_inputs WHERE session_id = ?', (session.id,)) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        ready.close()
        results.close()


def _legacy_with_history(tmp_path: Path) -> tuple[Path, str]:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from src.engine.sessions.session_store import SessionStore

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'legacy')
    session = store.create_session(title='preserved title')
    session.messages = [
        ModelRequest(parts=[UserPromptPart(content='preserved question')]),
        ModelResponse(parts=[TextPart(content='preserved answer')]),
    ]
    store.save(session)
    path = store.database.path
    for table in ('input_deliveries', 'recovery_links', 'roadmap_revisions'):
        store.database.execute(f'DROP TABLE {table}')
    store.database.execute('DROP TABLE accepted_inputs')
    store.database.execute('PRAGMA user_version = 5')
    Database.forget(path)
    return path, session.id


def _snapshot(path: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    with sqlite3.connect(path) as conn:
        schema = conn.execute('SELECT type, name, sql FROM sqlite_master ORDER BY type, name').fetchall()
        sessions = conn.execute('SELECT * FROM sessions ORDER BY id').fetchall()
        messages = conn.execute('SELECT * FROM messages ORDER BY execution_id, seq').fetchall()
    return schema, sessions, messages


def test_schema_failure_rolls_back_actual_ddl_and_preserves_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.engine.storage import database as module

    path, session_id = _legacy_with_history(tmp_path)
    before_schema, before_sessions, before_messages = _snapshot(path)
    original = module._execute_statements

    def fail_after_schema(conn: sqlite3.Connection, script: str) -> None:
        if script == module.SCHEMA:
            original(conn, script + '\nINVALID SQL;')
        else:
            original(conn, script)

    with monkeypatch.context() as patch:
        patch.setattr(module, '_execute_statements', fail_after_schema)
        with pytest.raises(sqlite3.OperationalError):
            Database(path)
    assert _snapshot(path) == (before_schema, before_sessions, before_messages)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 5
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='accepted_inputs'").fetchone()[0] == 0
    db = Database(path)
    assert db.value('PRAGMA user_version') == USER_VERSION
    assert db.value("SELECT count(*) FROM sqlite_master WHERE name='accepted_inputs'") == 1
    assert [tuple(row) for row in db.query('SELECT * FROM sessions ORDER BY id')] == before_sessions
    assert [tuple(row) for row in db.query('SELECT * FROM messages ORDER BY execution_id, seq')] == before_messages
    assert db.value('SELECT title FROM sessions WHERE id = ?', (session_id,)) == 'preserved title'
    db.close()


def _hold_schema(path: Path, held: Any, release: Any, result: Any) -> None:
    from src.engine.storage import database as module

    original = module._execute_statements

    def pause_in_schema(conn: sqlite3.Connection, script: str) -> None:
        if script == module.SCHEMA:
            held.set()
            if not release.wait(10):
                raise TimeoutError('migration lock was not released')
        original(conn, script)

    module._execute_statements = pause_in_schema
    db = Database(path)
    result.put(db.value('PRAGMA user_version'))
    db.close()


def _compete_schema(path: Path, held: Any, attempted: Any, completed: Any, result: Any) -> None:
    from src.engine.storage import database as module

    if not held.wait(10):
        raise TimeoutError('first migrator did not obtain the lock')
    original_connect = sqlite3.connect
    events: list[tuple[str, int | None]] = []

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            cursor = super().execute(sql, parameters)
            if sql == 'BEGIN IMMEDIATE':
                events.append(('lock_acquired', None))
            elif sql == 'PRAGMA user_version':
                version = cursor.fetchone()[0]
                name = 'version_after_lock' if self.in_transaction else 'version_before_lock'
                events.append((name, version))
                return super().execute(sql, parameters)
            return cursor

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = original_connect(*args, **kwargs, factory=ObservedConnection)
        conn.set_trace_callback(lambda sql: attempted.set() if sql == 'BEGIN IMMEDIATE' else None)
        return conn

    def unexpected_migration(conn: sqlite3.Connection, script: str) -> None:
        raise AssertionError('second initializer attempted redundant migration')

    module.sqlite3.connect = connect
    module._execute_statements = unexpected_migration
    db = Database(path)
    observed = list(events)
    result.put((db.value('PRAGMA user_version'), observed))
    completed.set()
    db.close()


def test_migration_processes_compete_under_writer_lock(tmp_path: Path) -> None:
    path, session_id = _legacy_with_history(tmp_path)
    _, sessions, messages = _snapshot(path)
    context = multiprocessing.get_context('spawn')
    held, release, attempted, completed = context.Event(), context.Event(), context.Event(), context.Event()
    result = context.Queue()
    first = context.Process(target=_hold_schema, args=(path, held, release, result))
    second = context.Process(target=_compete_schema, args=(path, held, attempted, completed, result))
    processes = (first, second)
    try:
        first.start()
        second.start()
        assert attempted.wait(10), 'second Database did not enter SQLite BEGIN IMMEDIATE'
        assert not completed.wait(0.25), 'second Database completed while the first held the lock'
        release.set()
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        outcomes = [result.get(timeout=2) for _ in processes]
        assert USER_VERSION in outcomes
        assert (
            USER_VERSION,
            [('version_before_lock', 5), ('lock_acquired', None), ('version_after_lock', USER_VERSION)],
        ) in outcomes
        assert completed.is_set()
        _, after_sessions, after_messages = _snapshot(path)
        assert after_sessions == sessions
        assert after_messages == messages
        assert any(row[0] == session_id for row in after_sessions)
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        result.close()
