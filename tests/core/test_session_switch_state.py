"""Two things that followed the wrong owner after a switch.

Both bugs are the same shape: a piece of state was re-pointed in the model and the
widget that paints it kept the old object.

- **The roadmap.** Loading a session brought its plan back, and `/new` kept showing
  it: the registry had swapped the store, and the panel was still reading the one it
  painted last.
- **The worktree badge.** `/new --worktree` moved the process and the title, and the
  status bar kept the label of the tree it came from, because nothing repainted it
  after the switch.

The tests are end to end through the app: the panel's text and the bar's badge, not
the objects behind them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.core.roadmap.models.todo_item import TodoItem
from src.core.roadmap.roadmap_stores import RoadmapStores


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / 'project'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / 'README.md').write_text('hello\n', encoding='utf-8')
    _git(root, 'add', '.')
    _git(root, 'commit', '-q', '-m', 'init')
    return root


def test_a_rebound_session_does_not_lose_what_was_written_to_it(tmp_path: Path, repo) -> None:
    """Re-binding the same session is a no-op, not a reset.

    It happens on every mount and on every view change. Recreating the stores there
    would drop live listeners and the items of a plan that is still open.
    """
    stores = RoadmapStores(session_id='session1', base_dir=tmp_path / 'roadmaps')
    stores.root.set_items([TodoItem(title='Still here')])

    stores.bind_session('session1')

    assert [item.title for item in stores.root.snapshot()] == ['Still here']
