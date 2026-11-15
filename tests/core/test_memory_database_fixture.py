from pathlib import Path

import pytest

from src.engine.storage.database import Database


@pytest.mark.usefixtures('memory_databases')
def test_memory_databases_share_a_path_and_isolate_different_paths(tmp_path: Path) -> None:
    first = Database.at(tmp_path / 'first.db')
    assert Database.at(tmp_path / 'first.db') is first
    assert Database.at(tmp_path / 'second.db') is not first
    assert first.value('PRAGMA foreign_keys') == 1
    assert first.connection.execute('PRAGMA database_list').fetchone()[2] == ''


@pytest.mark.usefixtures('memory_databases')
def test_memory_databases_start_empty_for_each_test(tmp_path: Path) -> None:
    assert Database.at(tmp_path / 'first.db').value('SELECT count(*) FROM workspaces') == 0
