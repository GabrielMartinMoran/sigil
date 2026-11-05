"""How much the working tree differs from `HEAD`, for the status bar.

The status bar answers one question at a glance: *how much have we changed?* The
numbers are the ones `git diff --stat` would print — additions and deletions of
tracked files, staged and unstaged together — plus the count of files git does not
track yet, which is exactly what an agent that just created a file leaves behind.

Untracked files are a count and not a line count on purpose: `git diff` has no
diff for a file outside the index, so any number there would be an invention of
this module rather than what git reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.shell.runner import execute_command

GIT_TIMEOUT = 10
"""Bound of each git call. The status bar refreshes after every tool call."""

MAX_UNTRACKED_FILES = 999
"""Untracked files are counted, not listed; this only caps a pathological tree."""

_GIT_ENV = {'GIT_OPTIONAL_LOCKS': '0'}
"""Read-only plumbing must not take the index lock while the agent may be writing."""

_BINARY_MARK = '-'


@dataclass(frozen=True, slots=True)
class DiffStat:
    """Additions and deletions of the working tree against `HEAD`."""

    added: int = 0
    removed: int = 0
    files: int = 0
    untracked: int = 0

    @property
    def total(self) -> int:
        """Changed lines. Zero means the tracked tree matches `HEAD`."""
        return self.added + self.removed

    @property
    def is_empty(self) -> bool:
        return self.total == 0 and self.untracked == 0


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _git(args: list[str], cwd: Path) -> RunToolOutput:
    command = 'git ' + ' '.join(_quote(arg) for arg in args)
    return execute_command(command, GIT_TIMEOUT, cwd=cwd, extra_env=_GIT_ENV, check_self_invocation=False)


def worktree_diff(root: str | os.PathLike[str] | None = None) -> DiffStat | None:
    """Diff summary of the repository at ``root``, or ``None`` outside a repository.

    ``None`` and an empty ``DiffStat`` mean different things and the bar shows
    them differently: no repository is "there is nothing to report", an empty diff
    is "+0 −0", which is still information.
    """
    base = Path(root) if root is not None else Path.cwd()
    inside = _git(['rev-parse', '--is-inside-work-tree'], base)
    if inside.returncode != 0 or inside.stdout.strip() != 'true':
        return None

    stat = _numstat(base)
    if stat is None:
        return None
    added, removed, files = stat
    return DiffStat(added=added, removed=removed, files=files, untracked=_untracked(base))


def _numstat(root: Path) -> tuple[int, int, int] | None:
    """Additions, deletions and file count of `git diff HEAD`.

    `HEAD` covers staged and unstaged changes in one call. A repository with no
    commits yet fails the comparison; there the tracked diff is empty and the
    untracked pass reports what is on disk.
    """
    result = _git(['diff', '--numstat', 'HEAD'], root)
    if result.returncode != 0:
        return (0, 0, 0) if _has_no_commits(result) else None

    added = removed = files = 0
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        files += 1
        if parts[0] == _BINARY_MARK or parts[1] == _BINARY_MARK:
            continue
        added += int(parts[0] or 0)
        removed += int(parts[1] or 0)
    return added, removed, files


def _has_no_commits(result: RunToolOutput) -> bool:
    return 'does not have any commits' in result.stderr or 'unknown revision' in result.stderr


def _untracked(root: Path) -> int:
    """Files git does not track and that are not ignored."""
    result = _git(['ls-files', '--others', '--exclude-standard', '-z'], root)
    if result.returncode != 0:
        return 0
    paths = [path for path in result.stdout.split('\0') if path]
    return min(len(paths), MAX_UNTRACKED_FILES)
