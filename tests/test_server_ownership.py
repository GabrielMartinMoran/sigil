"""Only one cooperating server owns a database, enforced by a real OS lock."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.server.ownership import OwnershipError, ServerOwnership

ROOT = Path(__file__).resolve().parents[1]


def _other_process(database: Path) -> subprocess.CompletedProcess[str]:
    script = (
        'from src.server.ownership import OwnershipError, ServerOwnership\n'
        f'try:\n    ServerOwnership({str(database)!r}).acquire()\n'
        "except OwnershipError as exc:\n    print('REFUSED', exc)\n"
        "else:\n    print('ACQUIRED')\n"
    )
    return subprocess.run(
        [sys.executable, '-c', script],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_a_second_server_is_refused_with_an_explicit_error(tmp_path: Path) -> None:
    database = tmp_path / 'sigil.db'
    owner = ServerOwnership(database)
    owner.acquire()
    try:
        result = _other_process(database)
        output = result.stdout + result.stderr
        assert 'REFUSED' in output
        assert str(database.with_name(f'{database.name}.server.lock')) in output
    finally:
        owner.release()


def test_releasing_the_lock_allows_the_next_owner(tmp_path: Path) -> None:
    database = tmp_path / 'sigil.db'
    first = ServerOwnership(database)
    first.acquire()
    first.release()

    second = ServerOwnership(database)
    second.acquire()
    second.release()


def test_a_symlinked_lock_path_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / 'sigil.db'
    target = tmp_path / 'target'
    target.write_text('', encoding='utf-8')
    database.with_name(f'{database.name}.server.lock').symlink_to(target)

    with pytest.raises(OwnershipError, match='not a safe regular file'):
        ServerOwnership(database).acquire()
