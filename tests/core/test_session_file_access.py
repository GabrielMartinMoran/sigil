from pathlib import Path

import pytest

from src.engine.files.models.file_tool_error import FileToolError
from src.engine.files.paths import resolve
from src.engine.sessions.session_store import SessionStore


def test_external_file_access_defaults_to_denied_and_can_be_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr('src.engine.files.paths.temp_dirs', lambda: ())
    external = tmp_path / 'external.txt'
    with pytest.raises(FileToolError):
        resolve(str(external))
    assert resolve(str(external), allow_external=True) == external


def test_external_file_access_persists_independently_per_session(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    first = store.create_session()
    second = store.create_session()
    assert not first.allow_external_files
    first.allow_external_files = True
    store.save(first)
    assert store.load(first.id).allow_external_files
    assert not store.load(second.id).allow_external_files
    first.allow_external_files = False
    store.save(first)
    assert not store.load(first.id).allow_external_files
