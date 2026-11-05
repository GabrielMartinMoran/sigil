"""Diff summary for the status bar.

The numbers have to agree with `git diff --stat`, so most of these tests build a
real repository and compare against git's own answer.
"""

from __future__ import annotations

import subprocess

from src.engine.workspace.diff import DiffStat, worktree_diff


def _git(cwd, *args: str) -> None:
    subprocess.run(
        ['git', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            'GIT_AUTHOR_NAME': 'Test',
            'GIT_AUTHOR_EMAIL': 'test@example.com',
            'GIT_COMMITTER_NAME': 'Test',
            'GIT_COMMITTER_EMAIL': 'test@example.com',
            'PATH': '/usr/bin:/bin:/usr/local/bin',
            'HOME': str(cwd),
        },
    )


def _repo(tmp_path):
    _git(tmp_path, 'init', '-q')
    (tmp_path / 'a.txt').write_text('one\ntwo\nthree\n', encoding='utf-8')
    _git(tmp_path, 'add', '.')
    _git(tmp_path, 'commit', '-q', '-m', 'init')
    return tmp_path


def test_outside_a_repository_there_is_no_summary(tmp_path):
    assert worktree_diff(tmp_path) is None


def test_a_clean_tree_is_zero_and_not_none(tmp_path):
    """`None` means "no repository"; zero means "nothing changed". The bar tells them apart."""
    stat = worktree_diff(_repo(tmp_path))
    assert stat is not None
    assert stat.added == 0
    assert stat.removed == 0
    assert stat.is_empty


def test_unstaged_changes_are_counted(tmp_path):
    root = _repo(tmp_path)
    (root / 'a.txt').write_text('one\ntwo\nthree\nfour\nfive\n', encoding='utf-8')
    stat = worktree_diff(root)
    assert stat is not None
    assert stat.added == 2
    assert stat.removed == 0
    assert stat.files == 1


def test_staged_changes_are_counted_too(tmp_path):
    """`git diff HEAD` covers the index: a staged edit is still an edit."""
    root = _repo(tmp_path)
    (root / 'a.txt').write_text('one\n', encoding='utf-8')
    _git(root, 'add', 'a.txt')
    stat = worktree_diff(root)
    assert stat is not None
    assert stat.removed == 2
    assert stat.added == 0


def test_additions_and_deletions_are_separate(tmp_path):
    root = _repo(tmp_path)
    (root / 'a.txt').write_text('one\nTWO\nthree\nfour\n', encoding='utf-8')
    stat = worktree_diff(root)
    assert stat is not None
    assert stat.added == 2
    assert stat.removed == 1
    assert stat.total == 3


def test_untracked_files_are_counted_and_not_guessed(tmp_path):
    """Git has no diff for a file outside the index, so only the count is real."""
    root = _repo(tmp_path)
    (root / 'new.txt').write_text('a\nb\nc\n', encoding='utf-8')
    stat = worktree_diff(root)
    assert stat is not None
    assert stat.untracked == 1
    # No invented line counts: the tracked diff is still empty.
    assert stat.added == 0
    assert stat.removed == 0
    assert not stat.is_empty


def test_ignored_files_are_not_counted(tmp_path):
    root = _repo(tmp_path)
    (root / '.gitignore').write_text('build/\n', encoding='utf-8')
    (root / 'build').mkdir()
    (root / 'build' / 'out.bin').write_text('x', encoding='utf-8')
    stat = worktree_diff(root)
    assert stat is not None
    # `.gitignore` itself is untracked; `build/` is not.
    assert stat.untracked == 1


def test_a_binary_file_does_not_break_the_count(tmp_path):
    root = _repo(tmp_path)
    (root / 'a.txt').write_bytes(b'\x00\x01\x02')
    stat = worktree_diff(root)
    assert stat is not None
    # `--numstat` reports `-` for binary files; the line count is skipped, the
    # file is still counted.
    assert stat.files == 1
    assert stat.added == 0


def test_a_repository_without_commits_reports_untracked_files(tmp_path):
    root = tmp_path / 'fresh'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / 'first.txt').write_text('hello\n', encoding='utf-8')
    stat = worktree_diff(root)
    assert stat is not None
    assert stat.untracked == 1
    assert stat.is_empty is False


def test_the_summary_is_built_from_the_requested_root(tmp_path):
    """The agent can work in a subdirectory; the summary follows the workspace root."""
    root = _repo(tmp_path)
    nested = root / 'pkg'
    nested.mkdir()
    (root / 'a.txt').write_text('one\n', encoding='utf-8')
    assert worktree_diff(nested) is not None
    stat = worktree_diff(nested)
    assert stat is not None
    assert stat.removed == 2


def test_an_empty_stat_has_a_zero_total():
    assert DiffStat().total == 0
    assert DiffStat().is_empty
