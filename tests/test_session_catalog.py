"""Tests for the session listing across the workspaces of a repository."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.engine.sessions.catalog import catalog, label_for, workspaces
from src.engine.sessions.session_store import SessionStore
from src.engine.workspace.repository import GitRepository


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / 'project'
    root.mkdir()
    _git(root, 'init', '-q')
    _git(root, 'commit', '-q', '--allow-empty', '-m', 'init')
    return root


def _session(store: SessionStore, text: str) -> str:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    session = store.create_session()
    session.messages = [ModelRequest(parts=[UserPromptPart(content=text)])]
    store.save(session)
    return session.id


def test_no_repository_means_a_single_workspace(tmp_path):
    plain = tmp_path / 'plain'
    plain.mkdir()
    assert [workspace.label for workspace in workspaces(plain)] == ['plain']
    assert label_for(plain) == 'plain'


def test_workspaces_are_labeled_main_and_by_worktree_name(repo, tmp_path):
    repository = GitRepository.discover(repo)
    assert repository is not None
    repository.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    listed = workspaces(repo)
    assert [workspace.label for workspace in listed] == ['main', 'fix-login']
    assert listed[0].is_worktree is False
    assert listed[1].is_worktree is True


def test_the_active_workspace_comes_first(repo, tmp_path):
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    listed = workspaces(created.path)
    assert listed[0].root == created.path
    assert listed[0].label == 'fix-login'
    assert listed[1].label == 'main'


def test_label_inside_a_worktree_is_not_main(repo, tmp_path):
    """Discovering from a linked worktree must not call it `main`.

    `git rev-parse --show-toplevel` answers with the linked worktree itself, so the
    label has to be derived from the shared `.git` instead.
    """
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    assert label_for(created.path) == 'fix-login'

    # A subdirectory is still the same workspace: Sigil can be started anywhere.
    inside = created.path / 'nested' / 'deeper'
    inside.mkdir(parents=True)
    assert label_for(inside) == 'fix-login'


def test_catalog_gathers_sessions_of_every_workspace(repo, tmp_path):
    base = tmp_path / 'sessions'
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    main_session = _session(SessionStore(cwd=repo, base_dir=base), 'task in main')
    worktree_session = _session(SessionStore(cwd=created.path, base_dir=base), 'task in the worktree')

    entries = catalog(repo, base_dir=base)
    by_id = {entry.session.id: entry for entry in entries}

    assert set(by_id) == {main_session, worktree_session}
    assert by_id[main_session].label == 'main'
    assert by_id[main_session].is_current is True
    assert by_id[worktree_session].label == 'fix-login'
    assert by_id[worktree_session].is_current is False
    assert by_id[worktree_session].is_worktree is True


def test_catalog_from_a_worktree_marks_it_as_current(repo, tmp_path):
    base = tmp_path / 'sessions'
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    main_session = _session(SessionStore(cwd=repo, base_dir=base), 'task in main')
    worktree_session = _session(SessionStore(cwd=created.path, base_dir=base), 'task in the worktree')

    entries = {entry.session.id: entry for entry in catalog(created.path, base_dir=base)}
    assert entries[worktree_session].is_current is True
    assert entries[main_session].is_current is False


def test_catalog_lists_newest_first_across_workspaces(repo, tmp_path):
    base = tmp_path / 'sessions'
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    _session(SessionStore(cwd=repo, base_dir=base), 'old')
    newest = _session(SessionStore(cwd=created.path, base_dir=base), 'new')

    entries = catalog(repo, base_dir=base)
    assert entries[0].session.id == newest


def test_the_same_session_in_two_workspaces_is_listed_once(repo, tmp_path):
    """A session id is one row, even when two workspaces have a copy of the file.

    The parent picker cannot hold two options with the same id —`OptionList`
    raises— so the catalog is where the id has to be unique: the newest copy wins
    and the rest are the same conversation by another name.
    """
    base = tmp_path / 'sessions'
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    main_store = SessionStore(cwd=repo, base_dir=base)
    session_id = _session(main_store, 'copied over')
    worktree_store = SessionStore(cwd=created.path, base_dir=base)
    shutil.copy(main_store.directory / f'{session_id}.json', worktree_store.directory / f'{session_id}.json')

    entries = catalog(repo, base_dir=base)
    listed = [entry.session.id for entry in entries]
    assert listed.count(session_id) == 1


def test_sessions_of_another_repository_are_not_listed(repo, tmp_path):
    base = tmp_path / 'sessions'
    other = tmp_path / 'unrelated'
    other.mkdir()
    _git(other, 'init', '-q')
    _git(other, 'commit', '-q', '--allow-empty', '-m', 'init')

    _session(SessionStore(cwd=repo, base_dir=base), 'mine')
    _session(SessionStore(cwd=other, base_dir=base), 'not mine')

    entries = catalog(repo, base_dir=base)
    assert [entry.session.preview for entry in entries] == ['mine']


def test_removed_worktree_no_longer_offers_its_sessions(repo, tmp_path):
    """A deleted worktree stops being a workspace: its folder is gone from the listing."""
    base = tmp_path / 'sessions'
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('temporary', base_dir=tmp_path / 'worktrees')
    session_id = _session(SessionStore(cwd=created.path, base_dir=base), 'gone')

    subprocess.run(['git', 'worktree', 'remove', '--force', str(created.path)], cwd=repo, check=True)

    entries = catalog(repo, base_dir=base)
    # The session file survives on disk, but the workspace it belonged to is no
    # longer part of the repository, so the listing does not offer it.
    assert session_id not in {entry.session.id for entry in entries}
