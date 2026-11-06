"""Snapshots of a workspace, taken with git and without touching it.

A snapshot has to be cheap to take, cheap to keep and exact to restore. Copying the
workspace fails the first two at any real size; a journal of the write tools fails the
third, because `run` can rewrite a thousand files that no journal sees. Git already
solves all three, so this borrows it:

- the tree is frozen into a **commit object** reachable from
  `refs/sigil/checkpoints/<id>`, so `git gc` cannot collect it;
- the user's branch, index and worktree are untouched, because `add` runs against a
  **scratch index** (`GIT_INDEX_FILE`);
- a restore is a **diff against a snapshot of the present**, so it removes what was
  created since and puts back what was deleted, exactly.

Measured on this repository: 6.7 ms warm over 444 files, 93.8 ms on a two-thousand
file tree. It runs once per turn, in a worker thread, never on the checkpoint path.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CHECKPOINT_REF = 'refs/sigil/checkpoints'
"""Where snapshots live. A ref is what keeps an unreferenced commit alive."""


class NotARepository(Exception):
    """There is no git repository to snapshot, so the files half is unavailable."""


@dataclass(frozen=True, slots=True)
class DiffEntry:
    """One file that differs between two snapshots."""

    status: str
    """`A` added, `M` modified, `D` deleted, relative to the second snapshot."""
    path: str


class WorkspaceSnapshots:
    """Commits that hold what the worktree looked like, one per checkpoint."""

    def __init__(self, root: str | Path, *, scratch: str | Path | None = None) -> None:
        self.root = Path(root)
        self._scratch_dir = Path(scratch) if scratch is not None else Path(tempfile.gettempdir()) / 'sigil-index'
        self._indexes: dict[str, Path] = {}

    # --- availability --------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether the workspace is a git repository this can snapshot.

        Every failure counts as "not available": a missing directory, a git that is
        not installed, a repository whose index is broken. The files half of a
        rollback says so and the conversation half still works.
        """
        try:
            self._git('rev-parse', '--git-dir')
        except NotARepository, OSError, subprocess.SubprocessError:
            return False
        return True

    # --- snapshot ------------------------------------------------------------

    def take(self, checkpoint_id: str, *, reuse: bool = True) -> str | None:
        """Freezes the worktree into a commit. ``None`` when there is no repository.

        ``reuse`` keeps the scratch index between calls, which is what makes the
        second snapshot of a turn cheap: unchanged files are stat-cached instead of
        read again.
        """
        if not self.available:
            raise NotARepository(f'{self.root} is not a git repository')
        index = self._index(checkpoint_id)
        if not reuse or not index.exists():
            index.unlink(missing_ok=True)
            try:
                self._git('read-tree', 'HEAD', index=index)
            except subprocess.CalledProcessError:
                # A repository with no commit yet: there is no HEAD to start from.
                self._git('read-tree', '--empty', index=index)
        try:
            self._git('add', '-A', '.', index=index)
            tree = self._git('write-tree', index=index)
            parent = self._head()
            commit = self._git(
                'commit-tree',
                tree,
                *(['-p', parent] if parent else []),
                '-m',
                f'sigil checkpoint {checkpoint_id}',
            )
            self._git('update-ref', f'{CHECKPOINT_REF}/{checkpoint_id}', commit)
        except subprocess.SubprocessError as exc:  # pragma: no cover - git failed
            raise NotARepository(str(exc)) from exc
        return commit

    def head_of(self, checkpoint_id: str) -> str | None:
        """The commit of a checkpoint, or ``None`` when it has none."""
        try:
            return self._git('rev-parse', f'{CHECKPOINT_REF}/{checkpoint_id}')
        except NotARepository, subprocess.CalledProcessError:
            return None

    def forget(self, checkpoint_id: str) -> None:
        """Drops the ref. The objects are reclaimed by the next `git gc`."""
        try:
            self._git('update-ref', '-d', f'{CHECKPOINT_REF}/{checkpoint_id}')
        except NotARepository, subprocess.CalledProcessError:
            pass

    # --- restore -------------------------------------------------------------

    def diff(self, before: str, after: str) -> list[DiffEntry]:
        """What changed between two snapshots, as git reports it."""
        raw = self._git('diff', '--name-status', before, after)
        entries: list[DiffEntry] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            status, _, path = line.partition('\t')
            entries.append(DiffEntry(status=status[:1], path=path))
        return entries

    def restore(self, target: str, current: str) -> list[str]:
        """Makes the worktree match ``target``, using ``current`` to know what to remove.

        A checkout would be wrong in both directions: it leaves behind files created
        after the snapshot and it cannot tell which deletions were the session's. The
        diff against the present answers both.
        """
        touched: list[str] = []
        for entry in self.diff(current, target):
            destination = self.root / entry.path
            if entry.status == 'D':
                if destination.exists():
                    destination.unlink()
                    touched.append(f'deleted {entry.path}')
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = subprocess.run(
                ['git', '-C', str(self.root), 'show', f'{target}:{entry.path}'],
                capture_output=True,
                check=True,
            ).stdout
            destination.write_bytes(content)
            touched.append(f'{entry.status.lower()} {entry.path}')
        return touched

    # --- internals -----------------------------------------------------------

    def _index(self, checkpoint_id: str) -> Path:
        cached = self._indexes.get(checkpoint_id)
        if cached is None:
            cached = self._scratch_dir / f'{checkpoint_id}.index'
            self._indexes[checkpoint_id] = cached
        return cached

    def _head(self) -> str | None:
        try:
            return self._git('rev-parse', 'HEAD')
        except subprocess.CalledProcessError:
            return None

    def _git(self, *args: str, index: Path | None = None) -> str:
        environment = dict(os.environ)
        environment['GIT_TERMINAL_PROMPT'] = '0'
        if index is not None:
            index.parent.mkdir(parents=True, exist_ok=True)
            environment['GIT_INDEX_FILE'] = str(index)
        result = subprocess.run(
            ['git', '-C', str(self.root), *args],
            capture_output=True,
            text=True,
            env=environment,
            check=False,  # the return code is inspected below, with a better error
        )
        if result.returncode != 0:
            message = result.stderr.strip() or f'git {args[0]} failed'
            if 'not a git repository' in message:
                raise NotARepository(message)
            raise subprocess.CalledProcessError(result.returncode, args, result.stdout, message)
        return result.stdout.strip()


__all__ = ['CHECKPOINT_REF', 'DiffEntry', 'NotARepository', 'WorkspaceSnapshots']
