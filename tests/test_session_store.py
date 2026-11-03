"""Tests de persistencia de sesiones (conversaciones)."""

import json

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from src.engine.sessions.session_store import SessionStore, cwd_hash


def _sample_messages():
    return [
        ModelRequest(parts=[UserPromptPart(content='hola')]),
        ModelResponse(parts=[TextPart(content='hey there')]),
    ]


# --- Hash por directorio ------------------------------------------------------


def test_cwd_hash_is_stable_and_short(tmp_path):
    first = cwd_hash(tmp_path)
    second = cwd_hash(tmp_path)
    assert first == second
    assert len(first) == 16


def test_different_cwds_get_different_directories(tmp_path):
    a = SessionStore(cwd=tmp_path / 'a', base_dir=tmp_path / 'store')
    b = SessionStore(cwd=tmp_path / 'b', base_dir=tmp_path / 'store')
    assert a.directory != b.directory
    assert a.directory.is_dir() and b.directory.is_dir()


# --- Persistencia -------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    session.messages = _sample_messages()
    store.save(session)

    loaded = store.load(session.id)
    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.messages[0].parts[0].content == 'hola'
    assert loaded.messages[1].parts[0].content == 'hey there'


def test_create_session_persists_file(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()

    path = store.directory / f'{session.id}.json'
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data['id'] == session.id
    assert data['messages'] == []


# --- Última sesión ------------------------------------------------------------


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


# --- Listado y búsqueda -------------------------------------------------------


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


# --- Robutez ----------------------------------------------------------------


def test_corrupt_session_file_is_ignored(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    (store.directory / 'broken.json').write_text('{not json')

    assert store.list_sessions() == []


def test_preview_uses_first_user_message(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    session.messages = _sample_messages()
    assert session.preview == 'hola'
