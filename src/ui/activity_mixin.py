"""Status bar state: whether a turn is running, where, and how much has changed.

The bar is repainted from three sources with very different rhythms:

- the turn, which flips between idle and running twice per message;
- the git diff, which changes whenever a tool writes a file and costs a couple of
  process spawns to read;
- the clock, which only matters while the spinner is turning.

They meet here so the widget stays a painter and the turn code stays about turns.
The diff is polled, not pushed: a tool can change the tree through a shell command
the file tools never see, so there is no event to subscribe to.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from textual import work

from src.engine.workspace.diff import DiffStat, worktree_diff
from src.ui.widgets.status_bar import ActivityState, StatusBar, WorkspaceBadge

DIFF_REFRESH_INTERVAL = 1.5
"""Seconds between diff polls. Faster than a human writes, slower than a busy loop."""


class ActivityMixin:
    """Keeps the status bar honest.

    It expects the app to define ``_current_run`` and either ``_workspace_manager``
    (in-process) or ``_service_state`` (attached). The two are exclusive: the
    terminal composes no workspace manager when the service owns the workspace, and
    an attached terminal that read a local manager would paint a place it is not in.
    """

    _workspace_manager: Any
    _service_state: dict[str, Any] | None
    _current_run: Any

    # --- activity -----------------------------------------------------------

    def set_activity(self, *, running: bool, label: str = '') -> None:
        """Starts or stops the spinner, and remembers when the turn started.

        The elapsed time is computed from a monotonic clock set here rather than
        from the widget, because the widget is rebuilt on a theme change and the
        start of the turn is not.
        """
        if running:
            self._turn_started_at = time.monotonic()
        self._activity_running = running
        self._activity_label = label
        self._paint_activity()

    def _paint_activity(self) -> None:
        try:
            bar = self.query_one(StatusBar)
        except Exception:  # noqa: BLE001 - the bar is not mounted yet
            return
        elapsed = time.monotonic() - self._turn_started_at if self._activity_running else 0.0
        bar.set_activity(ActivityState(running=self._activity_running, label=self._activity_label, elapsed=elapsed))

    def activity_tick(self) -> None:
        """Repaints the elapsed time. Called once a second by the turn."""
        if self._activity_running:
            self._paint_activity()

    def set_activity_label(self, label: str) -> None:
        """Changes the verb while the turn runs ("Thinking", "Running tool")."""
        self._activity_label = label
        if self._activity_running:
            self._paint_activity()

    # --- workspace ----------------------------------------------------------

    def workspace_badge(self) -> WorkspaceBadge | None:
        """The worktree and branch the agent is working on.

        In-process the manager answers; attached, the service's projection does.
        No projection yet means no badge: a worktree the terminal invented from a
        local path would be a second truth about where the conversation runs.
        """
        manager = getattr(self, '_workspace_manager', None)
        if manager is not None:
            workspace = manager.current()
            return WorkspaceBadge(
                label=workspace.label,
                branch=workspace.branch,
                is_worktree=workspace.is_worktree,
            )
        reported = self._reported_workspace()
        if reported is None:
            return None
        branch = reported.get('branch')
        return WorkspaceBadge(
            label=str(reported.get('label') or ''),
            branch=str(branch) if isinstance(branch, str) else None,
            is_worktree=bool(reported.get('is_worktree')),
        )

    def _reported_workspace(self) -> dict[str, Any] | None:
        """The workspace the service says it is running in, when it has said."""
        state = getattr(self, '_service_state', None) or {}
        workspace = state.get('workspace') if isinstance(state, dict) else None
        return workspace if isinstance(workspace, dict) else None

    def refresh_workspace_badge(self) -> None:
        try:
            bar = self.query_one(StatusBar)
        except Exception:  # noqa: BLE001
            return
        bar.set_project(self._project_name())
        bar.set_workspace(self.workspace_badge())

    def _project_name(self) -> str:
        """The project the bar names: the manager's root, or the service's own."""
        manager = getattr(self, '_workspace_manager', None)
        if manager is not None:
            return manager.root.name
        reported = self._reported_workspace()
        root = (reported or {}).get('root')
        return Path(str(root)).name if root else ''

    # --- diff ---------------------------------------------------------------

    @work(group='diff')
    async def refresh_diff(self) -> None:
        """Refreshes the diff and branch off-thread; overlapping polls share the read.

        Cancelling an exclusive async worker cannot stop its thread. Coalescing
        instead prevents a burst of tool events from spawning concurrent Git reads.
        """
        root = self._workspace_root()
        if root is None or getattr(self, '_diff_reading', False):
            return
        self._diff_reading = True
        try:
            workspace = await asyncio.to_thread(self.workspace_badge)
            diff = await asyncio.to_thread(worktree_diff, root)
        except Exception:  # noqa: BLE001 - the bar is a summary, never a failure
            return
        finally:
            self._diff_reading = False
        if root != self._workspace_root():
            return
        self._last_diff = diff
        try:
            bar = self.query_one(StatusBar)
            bar.set_workspace(workspace)
            bar.set_diff(diff)
        except Exception:  # noqa: BLE001
            return

    def _workspace_root(self) -> str | None:
        """The tree to diff: the manager's root, or the one the service reports."""
        manager = getattr(self, '_workspace_manager', None)
        if manager is not None:
            return str(manager.root)
        reported = self._reported_workspace()
        root = (reported or {}).get('root')
        return str(root) if root else None

    @property
    def last_diff(self) -> DiffStat | None:
        """Last diff read, for tests and for `/context`."""
        return getattr(self, '_last_diff', None)
