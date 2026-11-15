"""One session of a workspace, as the picker lists it."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.sessions.session_summary import SessionSummary
from src.core.workspace.models.workspace import Workspace


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A session and the workspace it was created in."""

    session: SessionSummary
    workspace: Workspace
    is_current: bool

    @property
    def label(self) -> str:
        """Workspace label shown next to the session."""
        return self.workspace.label

    @property
    def is_worktree(self) -> bool:
        return self.workspace.is_worktree
