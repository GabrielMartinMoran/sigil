"""Status bar: what the agent is doing, where, and how much it has changed.

The bar answers three questions in one line, left to right:

- **Is it working?** An animated spinner and the elapsed time while a turn runs, and
  a quiet resting mark when it does not. A chat that is waiting on a model looks
  identical to one that is waiting on the user, and the difference matters: it
  decides whether to wait or to type.
- **Where?** The worktree and the branch the agent is working on. With worktrees in
  the picture, two sessions look the same and write to different trees.
- **How much?** `+added −removed` for the working tree, in the same green and red
  `git diff --stat` uses, plus a count of files git does not track yet.

The right-hand side is right-aligned, so the bar reads as two groups instead of one
sentence that grows and shrinks with the branch name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.widgets import Static

from src.engine.workspace.diff import DiffStat
from src.ui.palette import color

SPINNER_FRAMES = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
"""Braille frames: one column each, so the bar does not jitter as it spins."""

SPINNER_INTERVAL = 0.1
"""Seconds per frame. Fast enough to read as motion, slow enough to stay calm."""

IDLE_MARK = '●'
"""Resting state. A dot, not a spinner frozen on a frame."""

MIN_GAP = 2
"""Columns kept between the left and right groups before the right one is dropped."""


@dataclass(frozen=True, slots=True)
class ActivityState:
    """Whether a turn is running and what it is doing right now."""

    running: bool = False
    label: str = ''
    elapsed: float = 0.0


@dataclass(frozen=True, slots=True)
class WorkspaceBadge:
    """The tree the agent is working in."""

    label: str = ''
    branch: str | None = None
    is_worktree: bool = False


class StatusBar(Static):
    """One-line status: activity, workspace and diff counters."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._activity = ActivityState()
        self._workspace: WorkspaceBadge | None = None
        self._diff: DiffStat | None = None
        self._frame = 0
        self._timer: Any = None

    # --- state --------------------------------------------------------------

    def set_activity(self, state: ActivityState) -> None:
        """Starts or stops the spinner. The timer only exists while it spins."""
        was_running = self._activity.running
        self._activity = state
        if state.running and not was_running:
            self._start_spinner()
        elif not state.running and was_running:
            self._stop_spinner()
        self.refresh()

    def set_workspace(self, workspace: WorkspaceBadge | None) -> None:
        self._workspace = workspace
        self.refresh()

    def set_diff(self, diff: DiffStat | None) -> None:
        """Diff summary. ``None`` means "not a repository", and nothing is painted."""
        self._diff = diff
        self.refresh()

    def _start_spinner(self) -> None:
        self._frame = 0
        if self._timer is None:
            self._timer = self.set_interval(SPINNER_INTERVAL, self._advance)

    def _stop_spinner(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(SPINNER_FRAMES)
        self.refresh()

    def on_unmount(self) -> None:
        self._stop_spinner()

    # --- rendering ----------------------------------------------------------

    def render(self) -> Text:
        """Left group, then the right group pushed to the edge.

        It renders from state instead of caching a string so a resize re-flows the
        two groups without anyone having to recompute them. It returns the left
        group alone while the bar has no width, which is the case before the first
        layout and in a bare unit test.
        """
        left = self._left()
        right = self._right()
        width = self.size.width
        if not right.plain or width <= 0:
            return left
        if len(left.plain) + len(right.plain) + MIN_GAP > width:
            # The bar is narrower than its content: the activity wins, because it is
            # the part that changes and the part the user is waiting on.
            return left
        gap = ' ' * (width - len(left.plain) - len(right.plain))
        return left + Text(gap) + right

    def plain(self) -> str:
        """The whole bar as one string, for tests and for diagnostics.

        `render` needs a laid-out width to place the right group; this joins the two
        groups so a test does not have to mount the widget to read them.
        """
        left, right = self._left(), self._right()
        if not right.plain:
            return left.plain
        return f'{left.plain}  {right.plain}'

    def _left(self) -> Text:
        state = self._activity
        text = Text()
        if state.running:
            text.append(f'{SPINNER_FRAMES[self._frame]} ', style=f'bold {color(self, "accent")}')
            text.append(state.label or 'Working', style='bold')
            if state.elapsed >= 1:
                text.append(f' {_elapsed(state.elapsed)}', style=color(self, 'muted'))
            return text
        text.append(f'{IDLE_MARK} ', style=color(self, 'muted'))
        text.append(state.label or 'Ready', style=color(self, 'muted'))
        return text

    def _right(self) -> Text:
        text = Text()
        workspace = self._workspace
        if workspace is not None and workspace.label:
            text.append(workspace.label, style='bold yellow' if workspace.is_worktree else 'bold cyan')
            if workspace.branch:
                text.append(f' {workspace.branch}', style=color(self, 'muted'))
        diff = self._diff
        if diff is not None:
            if text.plain:
                text.append('  ', style=color(self, 'muted'))
            text.append(f'+{diff.added}', style=color(self, 'diff-added'))
            text.append(' ', style=color(self, 'muted'))
            text.append(f'−{diff.removed}', style=color(self, 'diff-removed'))
            if diff.untracked:
                # Spelled out instead of a symbol: `1?` is a puzzle, `1 new` is not.
                text.append(f'  {diff.untracked} new', style=color(self, 'muted'))
        return text


def _elapsed(seconds: float) -> str:
    """`7s`, `1m04s`. Short, and it never grows past a few columns."""
    total = int(seconds)
    if total < 60:
        return f'{total}s'
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f'{minutes}m{rest:02d}s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h{minutes:02d}m'
