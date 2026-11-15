"""A workspace and its optional backing worktree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.workspace.models.worktree import Worktree


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
