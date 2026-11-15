"""The workspace label the status bar paints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceBadge:
    """The tree the agent is working in."""

    label: str = ''
    branch: str | None = None
    is_worktree: bool = False
