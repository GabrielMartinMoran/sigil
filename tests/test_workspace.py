"""Tests for worktree discovery, creation and workspace switching."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.engine.sessions.session_store import SessionStore
from src.engine.workspace.arguments import UsageError, parse_new_args
from src.engine.workspace.manager import WorkspaceManager
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.engine.workspace.naming import branch_for, validate_slug, worktree_path
from src.engine.workspace.repository import GitRepository


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one commit, so worktrees can branch from HEAD."""
    root = tmp_path / 'project'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / 'README.md').write_text('hello\n')
    _git(root, 'add', '.')
    _git(root, 'commit', '-q', '-m', 'init')
    return root


def _switch_back(monkeypatch, path: Path) -> None:
    """`chdir` is global: the rest of the suite expects the repo root."""
    monkeypatch.chdir(path)


# --- naming ------------------------------------------------------------------


def test_validate_slug_normalizes_case_and_spaces():
    assert validate_slug('  Fix-Login ') == 'fix-login'


@pytest.mark.parametrize('slug', ['', '   ', 'with space', 'UPPER_case', 'trailing-', '-leading', 'a' * 41, 'ñandú'])
def test_validate_slug_rejects_what_git_or_the_shell_would_hate(slug: str):
    with pytest.raises(WorkspaceError):
        validate_slug(slug)


def test_branch_and_path_are_namespaced_per_repository(tmp_path):
    assert branch_for('fix-login') == 'sigil/fix-login'
    assert worktree_path(tmp_path, 'project', 'fix-login') == tmp_path / 'project' / 'fix-login'


# --- discovery ---------------------------------------------------------------


def test_discover_returns_none_outside_a_work_tree(tmp_path):
    assert GitRepository.discover(tmp_path) is None


def test_discover_reads_the_repository_root(repo):
    found = GitRepository.discover(repo)
    assert found is not None
    assert found.toplevel == Path(os.path.realpath(repo))
    assert found.name == 'project'


def test_main_worktree_is_the_only_one_at_first(repo):
    repository = GitRepository.discover(repo)
    assert repository is not None
    worktrees = repository.worktrees()
    assert len(worktrees) == 1
    assert worktrees[0].is_main is True
    assert worktrees[0].name == 'main'
    assert worktrees[0].branch is not None


def test_linked_worktree_keeps_the_same_common_dir(repo, tmp_path):
    main = GitRepository.discover(repo)
    assert main is not None
    created = main.create_worktree('fix-login', base_dir=tmp_path / 'worktrees')

    assert created.path == tmp_path / 'worktrees' / 'project' / 'fix-login'
    assert created.is_main is False
    assert created.branch == 'sigil/fix-login'
    assert created.name == 'fix-login'

    # Discovering from inside the linked worktree must still see the repository.
    inside = GitRepository.discover(created.path)
    assert inside is not None
    assert inside.common_dir == main.common_dir
    assert inside.main_root == main.toplevel
    assert inside.name == 'project'


def test_creating_a_worktree_registers_it(repo, tmp_path):
    repository = GitRepository.discover(repo)
    assert repository is not None
    repository.create_worktree('second', base_dir=tmp_path / 'worktrees')
    names = [worktree.name for worktree in repository.worktrees()]
    assert names == ['main', 'second']


def test_creation_happens_in_the_target_repository_not_in_the_process_cwd(repo, tmp_path, monkeypatch):
    """The process cwd may sit inside another repository (Sigil itself, a test).

    `git worktree add` runs in the worktree it was discovered from, so a worktree
    requested for `repo` cannot end up registered in whichever repository happens
    to contain the process.
    """
    other = tmp_path / 'other'
    other.mkdir()
    _git(other, 'init', '-q')
    _git(other, 'commit', '-q', '--allow-empty', '-m', 'init')
    monkeypatch.chdir(other)

    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('scoped', base_dir=tmp_path / 'worktrees')

    assert created.path == tmp_path / 'worktrees' / 'project' / 'scoped'
    assert Path(created.path, 'README.md').is_file()
    # The repository that only happened to host the process stayed untouched.
    outsider = GitRepository.discover(other)
    assert outsider is not None
    assert [worktree.name for worktree in outsider.worktrees()] == ['main']


def test_duplicate_slug_is_rejected_before_touching_git(repo, tmp_path):
    repository = GitRepository.discover(repo)
    assert repository is not None
    repository.create_worktree('dup', base_dir=tmp_path / 'worktrees')
    with pytest.raises(WorkspaceError, match='already exists'):
        repository.create_worktree('dup', base_dir=tmp_path / 'worktrees')


def test_occupied_directory_is_rejected(repo, tmp_path):
    repository = GitRepository.discover(repo)
    assert repository is not None
    occupied = tmp_path / 'worktrees' / 'project' / 'taken'
    occupied.mkdir(parents=True)
    (occupied / 'file.txt').write_text('x')
    with pytest.raises(WorkspaceError, match='already exists'):
        repository.create_worktree('taken', base_dir=tmp_path / 'worktrees')


def test_parsing_a_detached_worktree_keeps_it_untagged(repo, tmp_path):
    repository = GitRepository.discover(repo)
    assert repository is not None
    created = repository.create_worktree('detached', base_dir=tmp_path / 'worktrees')
    _git(created.path, 'checkout', '-q', '--detach')

    worktrees = {worktree.name: worktree for worktree in repository.worktrees()}
    assert worktrees['detached'].is_detached is True
    assert worktrees['detached'].label == f'detached@{worktrees["detached"].short_head}'


# --- manager -----------------------------------------------------------------


def test_manager_defaults_to_cwd(repo, monkeypatch):
    monkeypatch.chdir(repo)
    manager = WorkspaceManager()
    assert manager.current().label == 'main'
    assert manager.current().is_worktree is False


def test_create_and_switch_moves_the_process_and_the_components(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(repo)
    sessions = SessionStore(cwd=repo, base_dir=tmp_path / 'sessions')
    manager = WorkspaceManager(repo, worktrees_dir=tmp_path / 'worktrees', session_store=sessions)

    switch = manager.create_and_switch('fix-login')

    assert switch.changed is True
    assert switch.created is not None
    assert switch.workspace.label == 'fix-login'
    assert switch.workspace.is_worktree is True
    assert Path.cwd() == switch.workspace.root
    # The session store followed the switch instead of staying in the old workspace.
    assert sessions.cwd == str(switch.workspace.root)
    assert sessions.database.value('SELECT path FROM workspaces WHERE id = ?', (sessions.workspace_id,)) == str(
        switch.workspace.root
    )


def test_switching_to_another_worktree_does_not_create_anything(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(repo)
    manager = WorkspaceManager(repo, worktrees_dir=tmp_path / 'worktrees')
    created = manager.create_worktree('other')
    assert Path.cwd() == repo  # creating does not activate

    switch = manager.switch_to_worktree(created)
    assert switch.created is None
    assert Path.cwd() == created.path
    assert switch.previous.label == 'main'


def test_a_failed_creation_leaves_the_workspace_untouched(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(repo)
    manager = WorkspaceManager(repo, worktrees_dir=tmp_path / 'worktrees')

    with pytest.raises(WorkspaceError):
        manager.create_and_switch('bad name')

    assert Path.cwd() == repo
    assert manager.current().label == 'main'


def test_switch_outside_a_repository_is_a_single_workspace(tmp_path, monkeypatch):
    plain = tmp_path / 'plain'
    plain.mkdir()
    monkeypatch.chdir(plain)
    manager = WorkspaceManager(plain)

    assert manager.current().label == 'plain'
    with pytest.raises(WorkspaceError, match='not a git repository'):
        manager.create_worktree('nope')


def test_switch_to_a_missing_directory_fails(repo, tmp_path):
    manager = WorkspaceManager(repo)
    with pytest.raises(WorkspaceError, match='does not exist'):
        manager.switch(tmp_path / 'missing')


def test_rebind_errors_do_not_abort_the_switch(repo, tmp_path, monkeypatch):
    class Broken:
        def rebind(self, root: object) -> None:
            raise RuntimeError('boom')

    monkeypatch.chdir(repo)
    manager = WorkspaceManager(repo, worktrees_dir=tmp_path / 'worktrees', rebindables=[Broken()])
    switch = manager.create_and_switch('resilient')

    assert switch.workspace.label == 'resilient'
    assert any('boom' in message for message in manager.rebind_errors)


def test_worktrees_dir_can_be_overridden_by_environment(monkeypatch, tmp_path):
    from src.engine.paths import worktrees_dir

    monkeypatch.setenv('SIGIL_WORKTREES_DIR', str(tmp_path / 'from-env'))
    manager = WorkspaceManager(tmp_path)
    assert manager.worktrees_dir == tmp_path / 'from-env'
    assert worktrees_dir() == tmp_path / 'from-env'


def test_worktrees_dir_defaults_under_the_sigil_home(monkeypatch, tmp_path):
    from src.engine.paths import worktrees_dir

    monkeypatch.delenv('SIGIL_WORKTREES_DIR', raising=False)
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    manager = WorkspaceManager(tmp_path)
    # Outside every repository is the point: a worktree inside one could be reached
    # by the file tools of another workspace.
    assert manager.worktrees_dir == tmp_path / 'home' / 'worktrees'
    assert worktrees_dir() == tmp_path / 'home' / 'worktrees'


# --- /new arguments ----------------------------------------------------------


def test_parse_new_without_arguments_keeps_the_current_workspace():
    assert parse_new_args('') == parse_new_args('   ')
    assert parse_new_args('').worktree is None


@pytest.mark.parametrize(
    'args',
    ['--worktree fix-login', '-w fix-login', '--worktree=fix-login', '-w=fix-login'],
)
def test_parse_new_accepts_the_worktree_forms(args: str):
    assert parse_new_args(args).worktree == 'fix-login'


@pytest.mark.parametrize(
    'args', ['--worktree', '-w', '--worktree=', '--nope', 'fix-login', '--worktree a --worktree b']
)
def test_parse_new_rejects_anything_else(args: str):
    with pytest.raises(UsageError):
        parse_new_args(args)
