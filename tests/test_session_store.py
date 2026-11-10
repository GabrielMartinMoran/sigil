"""Tests for session (conversation) persistence."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from src.engine.sessions.session_store import SessionStore, cwd_hash


def _sample_messages():
    return [
        ModelRequest(parts=[UserPromptPart(content='hello')]),
        ModelResponse(parts=[TextPart(content='hey there')]),
    ]


# --- Per-directory hash -------------------------------------------------------


def test_cwd_hash_is_stable_and_short(tmp_path):
    first = cwd_hash(tmp_path)
    second = cwd_hash(tmp_path)
    assert first == second
    assert len(first) == 16


def test_different_cwds_get_different_workspace_rows(tmp_path):
    """Two directories are two workspaces in the same database."""
    a = SessionStore(cwd=tmp_path / 'a', base_dir=tmp_path / 'store')
    b = SessionStore(cwd=tmp_path / 'b', base_dir=tmp_path / 'store')

    assert a.workspace_id != b.workspace_id
    assert a.database.path == b.database.path


# --- Persistence --------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    session.messages = _sample_messages()
    store.save(session)

    loaded = store.load(session.id)
    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.messages[0].parts[0].content == 'hello'
    assert loaded.messages[1].parts[0].content == 'hey there'


def test_create_session_persists_a_row(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()

    row = store.database.one('SELECT id, preview FROM sessions WHERE id = ?', (session.id,))
    assert row is not None
    assert row['id'] == session.id
    assert row['preview'] == ''
    # A session always has its root execution, from the first write.
    assert store.database.value('SELECT count(*) FROM agent_executions WHERE session_id = ?', (session.id,)) == 1


# --- Last session -------------------------------------------------------------


def test_load_last_returns_none_when_empty(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    assert store.load_last() is None


def test_load_last_returns_most_recently_saved(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    first = store.create_session()
    first.messages = _sample_messages()
    store.save(first)

    second = store.create_session()
    second.messages = _sample_messages()
    store.save(second)

    last = store.load_last()
    assert last is not None
    assert last.id == second.id


def test_new_store_instance_reads_last_session(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    session.messages = _sample_messages()
    store.save(session)

    reopened = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    last = reopened.load_last()
    assert last is not None
    assert last.id == session.id
    assert len(last.messages) == 2


# --- Listing and search -------------------------------------------------------


def test_list_sessions_returns_all_and_ignores_index(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    store.create_session()
    store.create_session()

    sessions = store.list_sessions()
    assert len(sessions) == 2


def test_find_by_id_prefix(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()

    assert [s.id for s in store.find(session.id[:4])] == [session.id]
    assert store.find('no-such-id') == []


def test_load_unknown_session_returns_none(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    assert store.load('does-not-exist') is None


# --- Robustness ---------------------------------------------------------------


def test_a_row_that_disappears_is_not_listed(tmp_path):
    """The store lists rows, so a session it cannot see is one it does not offer."""
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    store.database.execute('DELETE FROM sessions WHERE id = ?', (session.id,))

    assert store.list_sessions() == []


def test_a_session_id_is_one_row(tmp_path):
    """The duplicate the file store had to filter is impossible now.

    `sessions.id` is the primary key, so two copies of one conversation cannot both
    be listed: the database refuses the second.
    """
    import sqlite3

    import pytest

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()

    with pytest.raises(sqlite3.IntegrityError):
        store.database.execute(
            'INSERT INTO sessions (id, workspace_id, created_at, updated_at) VALUES (?, ?, ?, ?)',
            (session.id, store.workspace_id, 'a', 'b'),
        )

    assert [listed.id for listed in store.list_sessions()] == [session.id]


def test_preview_uses_first_user_message(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    session.messages = _sample_messages()
    assert session.preview == 'hello'


def test_stale_writer_must_not_discard_newer_conversation(tmp_path):
    import pytest
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from src.engine.sessions.session_store import SessionConflictError

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    first = store.create_session()
    first.messages = [ModelRequest(parts=[UserPromptPart('initial')])]
    store.save(first)
    stale = store.load(first.id)
    assert stale is not None
    first.messages.append(ModelRequest(parts=[UserPromptPart('newer')]))
    store.save(first)
    with pytest.raises(SessionConflictError):
        store.save(stale)
    loaded = store.load(first.id)
    assert loaded is not None
    assert len(loaded.messages) == 2


def test_stale_writer_cannot_replace_divergent_same_length_history(tmp_path):
    import pytest
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from src.engine.sessions.session_store import SessionConflictError

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    first = store.create_session()
    stale = store.load(first.id)
    assert stale is not None
    first.messages = [ModelRequest(parts=[UserPromptPart('newer')])]
    store.save(first)
    stale.messages = [ModelRequest(parts=[UserPromptPart('different')])]
    with pytest.raises(SessionConflictError):
        store.save(stale)
    loaded = store.load(first.id)
    assert loaded is not None
    assert loaded.messages[0].parts[0].content == 'newer'


def test_draft_is_not_saved_until_a_user_message(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.draft_session()
    store.save_progress(session)
    assert store.load(session.id) is None
    assert store.list_sessions() == []
    session.messages = [ModelRequest(parts=[UserPromptPart(content='hello')])]
    store.save_progress(session)
    assert store.load(session.id).messages[0].parts[0].content == 'hello'
