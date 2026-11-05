"""A git worktree and the workspace that hangs off it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAIN_NAME = 'main'
"""Label of the repository's main worktree; every other one uses its directory name."""

_SHORT_SHA_LENGTH = 7


@dataclass(frozen=True, slots=True)
class Worktree:
    """One worktree of a repository, as git reports it.

    `is_main` marks the worktree that owns the ``.git`` directory; the rest are
    linked worktrees created with ``git worktree add``.
    """

    path: Path
    head: str | None = None
    branch: str | None = None
    is_main: bool = False
    is_locked: bool = False
    is_bare: bool = False

    @property
    def name(self) -> str:
        """Identifier shown in listings.

        The main worktree is always ``main``: its directory name is the
        repository name, which would be redundant next to the sessions of its own
        worktrees.
        """
        if self.is_main:
            return MAIN_NAME
        return self.path.name or str(self.path)

    @property
    def is_detached(self) -> bool:
        return self.branch is None

    @property
    def short_head(self) -> str:
        return self.head[:_SHORT_SHA_LENGTH] if self.head else ''

    @property
    def label(self) -> str:
        """``main`` or the worktree name, plus the branch when it is detached."""
        if self.is_detached:
            return f'{self.name}@{self.short_head or "detached"}'
        return self.name

    def describe(self) -> str:
        """One-line description for diagnostics."""
        branch = self.branch or f'detached at {self.short_head}'
        return f'{self.label} ({self.path}, {branch})'


@dataclass(frozen=True, slots=True)
class Workspace:
    """Directory Sigil works in, with the worktree that backs it when there is one."""

    root: Path
    worktree: Worktree | None = None

    @property
    def label(self) -> str:
        """Identifier of the workspace: ``main``, a worktree name, or the directory."""
        if self.worktree is not None:
            return self.worktree.label
        return self.root.name or str(self.root)

    @property
    def is_worktree(self) -> bool:
        """Whether this workspace is a linked worktree (the main one is not)."""
        return self.worktree is not None and not self.worktree.is_main

    @property
    def branch(self) -> str | None:
        return self.worktree.branch if self.worktree is not None else None
