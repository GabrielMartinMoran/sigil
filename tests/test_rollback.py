"""Rollback: the conversation, the plan and the files, in one command.

The cases here are the ones the design promised: the point is before the turn, the
turn itself goes, a subagent summoned by a dropped turn goes with it, the plan comes
back from its snapshot, the undo puts everything back, and a repository is optional.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.engine.agents.rollback import Checkpoints, Rollback, RollbackError
from src.engine.agents.snapshots import WorkspaceSnapshots
from src.engine.agents.tree.tree import AgentTree
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.database import Database, database_path


def _turn(index: int, text: str = '', *, run: str | None = None) -> list:
    """One turn of a conversation: the prompt and the answer."""
    return [
        ModelRequest(parts=[UserPromptPart(content=text or f'question {index}')], run_id=run or f'run-{index}'),
        ModelResponse(parts=[TextPart(content=f'answer {index}')], run_id=run or f'run-{index}'),
    ]


def _session(tmp_path: Path) -> tuple[SessionStore, object]:
    base = tmp_path / 'home'
    Database.forget(database_path(base))
    store = SessionStore(cwd=tmp_path / 'workspace', base_dir=base)
    return store, store.create_session()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / 'workspace'
    repo.mkdir(parents=True)
    _git(repo, 'init', '-q', '-b', 'main')
    _git(repo, 'config', 'user.email', 't@e.com')
    _git(repo, 'config', 'user.name', 't')
    (repo / 'app.py').write_text('print("one")\n')
    (repo / '.gitignore').write_text('node_modules/\n')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'initial')
    return repo


# --- the points ---------------------------------------------------------------


def test_every_user_turn_becomes_a_checkpoint(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1) + _turn(2)
    store.save(session)

    checkpoints = Checkpoints(store.database, store.blobs).of_session(session.id)
    assert len(checkpoints) == 3
    assert all(item.reason == 'prompt' for item in checkpoints)
    assert all(item.turn_id is not None for item in checkpoints)


def test_a_steer_does_not_add_a_point(tmp_path):
    """A prompt typed while the agent works is inside the turn, so it is not a point."""
    store, session = _session(tmp_path)
    session.messages = _turn(0)
    store.save(session)
    first = Checkpoints(store.database, store.blobs).of_session(session.id)

    # The same run, one more prompt: a steer.
    session.messages += _turn(1, 'steer', run='run-0')
    store.save(session)

    assert Checkpoints(store.database, store.blobs).of_session(session.id) == first


def test_the_points_are_listed_newest_first(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    store.save(session)

    points = Rollback(store, workspace=tmp_path / 'workspace').points(session.id)
    assert [item.at for item in points] == sorted((item.at for item in points), reverse=True)


# --- the conversation half ----------------------------------------------------


def test_rolling_back_drops_the_turn_and_everything_after_it(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1) + _turn(2)
    store.save(session)
    points = Rollback(store, workspace=tmp_path / 'workspace').points(session.id)
    target = points[1]  # the second turn, in time order

    outcome = Rollback(store, workspace=tmp_path / 'workspace').to_turn(session.id, target.turn_id, files=False)

    assert outcome.turns_dropped == 2
    assert outcome.messages_dropped == 4
    # Not asked, which is not the same as could not: the flag reports a failure.
    assert outcome.files_unavailable is False

    loaded = store.load(session.id)
    assert len(loaded.messages) == 2
    assert 'question 0' in str(loaded.messages[0].parts[0].content)


def test_a_subagent_summoned_by_the_dropped_turn_goes_with_it(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    session.tree = AgentTree()
    session.tree.ensure_root('wizard', node_id=session.root_id)
    store.save(session)

    # The subagent is created during the second turn.
    second_turn = store.database.value(
        'SELECT id FROM turns WHERE execution_id = ? ORDER BY started_at DESC LIMIT 1', (session.root_id,)
    )
    store.database.execute(
        'INSERT INTO agent_executions (id, session_id, parent_id, created_by_turn, name, role, depth, status, started_at) '
        "VALUES ('child1', ?, ?, ?, 'scout', 'subagent', 1, 'done', 0)",
        (session.id, session.root_id, second_turn),
    )
    store.database.execute(
        'INSERT INTO turns (id, execution_id, origin, started_at, finished_at, outcome) '
        "VALUES ('cturn', 'child1', 'user', 't', 't', 'done')"
    )
    store.database.execute(
        "INSERT INTO messages (execution_id, turn_id, seq, kind, payload) VALUES ('child1', 'cturn', 0, 'request', '{}')"
    )

    Rollback(store, workspace=tmp_path / 'workspace').to_turn(session.id, second_turn, files=False)

    assert store.database.value("SELECT count(*) FROM agent_executions WHERE id = 'child1'") == 0
    assert store.database.value("SELECT count(*) FROM messages WHERE execution_id = 'child1'") == 0


def test_the_plan_comes_back_from_its_snapshot(tmp_path):
    """A cascade cannot restore a plan: an item belongs to an execution, not a turn."""
    store, session = _session(tmp_path)
    plans = RoadmapStores(session_id=session.id, base_dir=tmp_path / 'home')
    session.messages = _turn(0)
    plans.root.set_items([TodoItem(title='step one')])
    store.save(session)

    # The turn starts (its checkpoint is written from the first save, before the
    # tools run), and only then does it add its own item. That is the real order.
    session.messages += _turn(1)
    store.save(session)
    plans.root.add_items([TodoItem(title='step two')])
    store.save(session)
    assert [item.title for item in plans.root.snapshot()] == ['step one', 'step two']

    points = Rollback(store, workspace=tmp_path / 'workspace').points(session.id)
    Rollback(
        store,
        workspace=tmp_path / 'workspace',
        on_plan_changed=plans.reload_all,
    ).to_turn(session.id, points[0].turn_id, files=False)

    assert [item.title for item in plans.root.snapshot()] == ['step one']


def test_rolling_back_while_a_turn_runs_is_refused(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0)
    store.save(session)
    turn_id = store.database.value('SELECT id FROM turns WHERE execution_id = ?', (session.root_id,))
    store.database.execute('UPDATE turns SET finished_at = NULL, outcome = NULL WHERE id = ?', (turn_id,))

    with pytest.raises(RollbackError, match='still running'):
        Rollback(store, workspace=tmp_path / 'workspace').to_turn(session.id, str(turn_id), files=False)


def test_an_unknown_turn_is_refused(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0)
    store.save(session)

    with pytest.raises(RollbackError, match='No turn'):
        Rollback(store, workspace=tmp_path / 'workspace').to_turn(session.id, 'nope', files=False)


# --- the files half -----------------------------------------------------------


def test_the_files_go_back_to_that_instant(tmp_path):
    repo = _repo(tmp_path)
    base = tmp_path / 'home'
    Database.forget(database_path(base))
    store = SessionStore(cwd=repo, base_dir=base)
    session = store.create_session()
    session.messages = _turn(0)
    store.save(session)
    rollback = Rollback(store, workspace=repo)

    # The snapshot of that instant, then a turn that changes the workspace.
    first = rollback.points(session.id)[0]
    commit = rollback.snapshots.take(first.turn_id)
    store.database.execute('UPDATE checkpoints SET tree_commit = ? WHERE turn_id = ?', (commit, first.turn_id))

    (repo / 'app.py').write_text('print("two")\n')
    (repo / 'new.py').write_text('added later\n')
    session.messages += _turn(1)
    store.save(session)

    outcome = rollback.to_turn(session.id, first.turn_id, files=True)

    assert outcome.files_unavailable is False
    assert outcome.files_restored >= 1
    assert outcome.files_deleted >= 1
    assert (repo / 'app.py').read_text() == 'print("one")\n'
    assert not (repo / 'new.py').exists()


def test_a_workspace_outside_a_repository_still_rolls_the_conversation(tmp_path):
    store, session = _session(tmp_path)  # cwd is a plain directory, no git
    session.messages = _turn(0) + _turn(1)
    store.save(session)
    points = Rollback(store, workspace=tmp_path / 'workspace').points(session.id)

    outcome = Rollback(store, workspace=tmp_path / 'workspace').to_turn(session.id, points[0].turn_id)

    assert outcome.files_unavailable is True
    assert outcome.turns_dropped == 1


def test_nothing_is_destroyed_without_a_copy(tmp_path):
    """The undo checkpoint exists before anything is touched, and survives the cut."""
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    store.save(session)
    rollback = Rollback(store, workspace=tmp_path / 'workspace')
    points = rollback.points(session.id)

    outcome = rollback.to_turn(session.id, points[0].turn_id, files=False)

    undo = None
    for checkpoint in rollback.checkpoints.of_session(session.id):
        if checkpoint.id == outcome.undo_checkpoint:
            undo = checkpoint
    assert undo is not None
    assert undo.reason == 'before_rollback'
    assert undo.turn_id is None  # which is what lets it survive the truncation


def test_a_rollback_can_be_undone(tmp_path):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    store.save(session)
    rollback = Rollback(store, workspace=tmp_path / 'workspace')
    points = rollback.points(session.id)
    before = len(store.load(session.id).messages)

    outcome = rollback.to_turn(session.id, points[0].turn_id, files=False)
    assert len(store.load(session.id).messages) < before

    rollback.undo(session.id, outcome.undo_checkpoint)

    assert len(store.load(session.id).messages) == before


# --- the snapshot -------------------------------------------------------------


def test_a_snapshot_does_not_touch_the_branch_or_the_index(tmp_path):
    repo = _repo(tmp_path)
    (repo / 'untracked.py').write_text('work in progress\n')
    status_before = _git(repo, 'status', '--porcelain')
    head_before = _git(repo, 'rev-parse', 'HEAD')

    snapshots = WorkspaceSnapshots(repo)
    commit = snapshots.take('cp1')

    assert commit
    assert _git(repo, 'status', '--porcelain') == status_before
    assert _git(repo, 'rev-parse', 'HEAD') == head_before
    assert _git(repo, 'rev-parse', 'refs/sigil/checkpoints/cp1') == commit


def test_a_snapshot_survives_a_gc(tmp_path):
    repo = _repo(tmp_path)
    snapshots = WorkspaceSnapshots(repo)
    commit = snapshots.take('cp1')

    _git(repo, 'gc', '--prune=now', '--quiet')

    assert snapshots.head_of('cp1') == commit


def test_ignored_files_are_not_snapshotted_nor_deleted(tmp_path):
    repo = _repo(tmp_path)
    (repo / 'node_modules').mkdir()
    (repo / 'node_modules' / 'big.js').write_text('ignored\n')
    snapshots = WorkspaceSnapshots(repo)
    first = snapshots.take('cp1')
    second = snapshots.take('cp2')

    snapshots.restore(first, second)

    assert (repo / 'node_modules' / 'big.js').exists()


def test_a_binary_comes_back_byte_for_byte(tmp_path):
    repo = _repo(tmp_path)
    payload = bytes(range(256)) * 40
    (repo / 'image.bin').write_bytes(payload)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'bin')
    snapshots = WorkspaceSnapshots(repo)
    commit = snapshots.take('cp1')

    (repo / 'image.bin').write_bytes(b'corrupted')
    current = snapshots.take('cp2')
    snapshots.restore(commit, current)

    assert (repo / 'image.bin').read_bytes() == payload


@pytest.mark.parametrize('files', [True, False])
def test_the_outcome_reports_what_happened(tmp_path, files):
    store, session = _session(tmp_path)
    session.messages = _turn(0) + _turn(1)
    store.save(session)
    rollback = Rollback(store, workspace=tmp_path / 'workspace')
    points = rollback.points(session.id)

    outcome = rollback.to_turn(session.id, points[0].turn_id, files=files)

    assert outcome.touched_files == (files and not outcome.files_unavailable)
    assert outcome.target_turn_id == points[0].turn_id
