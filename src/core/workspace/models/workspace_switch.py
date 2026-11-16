"""Outcome of a workspace change."""

from dataclasses import dataclass

from src.core.workspace.models.workspace import Workspace
from src.core.workspace.models.worktree import Worktree


@dataclass(frozen=True, slots=True)
class WorkspaceSwitch:
    """Outcome of a switch, with what the UI needs to announce it."""

    workspace: Workspace
    previous: Workspace
    created: Worktree | None = None

    @property
    def changed(self) -> bool:
        return self.workspace.root != self.previous.root
