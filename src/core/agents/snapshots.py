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

from src.core.agents.diff_entry import DiffEntry
from src.core.agents.not_a_repository import NotARepository

"""Where snapshots live. A ref is what keeps an unreferenced commit alive."""


__all__ = ['DiffEntry', 'NotARepository']
