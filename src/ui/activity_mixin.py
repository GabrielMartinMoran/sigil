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
from typing import Any

from textual import work

from src.engine.workspace.diff import DiffStat, worktree_diff
from src.ui.widgets.status_bar import ActivityState, StatusBar, WorkspaceBadge

DIFF_REFRESH_INTERVAL = 1.5
"""Seconds between diff polls. Faster than a human writes, slower than a busy loop."""


class ActivityMixin:
    """Keeps the status bar honest.

    It expects the app to define ``_workspace_manager`` and ``_current_run``.
    """

    _workspace_manager: Any
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
        """The worktree and branch the agent is working on."""
        manager = getattr(self, '_workspace_manager', None)
        if manager is None:
            return None
        workspace = manager.current()
        return WorkspaceBadge(
            label=workspace.label,
            branch=workspace.branch,
            is_worktree=workspace.is_worktree,
        )

    def refresh_workspace_badge(self) -> None:
        try:
            bar = self.query_one(StatusBar)
        except Exception:  # noqa: BLE001
            return
        bar.set_workspace(self.workspace_badge())

    # --- diff ---------------------------------------------------------------

    @work(group='diff')
    async def refresh_diff(self) -> None:
        """Reads Git in a thread; overlapping polls share the in-flight read.

        Cancelling an exclusive async worker cannot stop its thread. Coalescing
        instead prevents a burst of tool events from spawning concurrent Git reads.
        """
        root = self._workspace_root()
        if root is None or getattr(self, '_diff_reading', False):
            return
        self._diff_reading = True
        try:
            diff = await asyncio.to_thread(worktree_diff, root)
        except Exception:  # noqa: BLE001 - the bar is a summary, never a failure
            return
        finally:
            self._diff_reading = False
        if root != self._workspace_root():
            return
        self._last_diff = diff
        try:
            self.query_one(StatusBar).set_diff(diff)
        except Exception:  # noqa: BLE001
            return

    def _workspace_root(self) -> str | None:
        manager = getattr(self, '_workspace_manager', None)
        if manager is None:
            return None
        return str(manager.root)

    @property
    def last_diff(self) -> DiffStat | None:
        """Last diff read, for tests and for `/context`."""
        return getattr(self, '_last_diff', None)
