"""How a node of the session tree is doing.

Four states, and they are exhaustive on purpose: a node is either still working
or it has stopped for one of three reasons. The UI paints them, the tree prunes
nothing, and a finished node keeps its state on disk so a resumed session tells
the truth about what ran and how it ended.
"""

from __future__ import annotations

from enum import StrEnum


class NodeStatus(StrEnum):
    """Lifecycle of one agent in the tree."""

    RUNNING = 'running'
    """The subagent is working: its run is open."""

    DONE = 'done'
    """It finished and sent its message back."""

    FAILED = 'failed'
    """It stopped on an error; the error is what its parent was told."""

    CANCELLED = 'cancelled'
    """It was stopped from outside: the turn was aborted, or the session switched."""

    @property
    def finished(self) -> bool:
        """Whether the node will not change state again on its own."""
        return self is not NodeStatus.RUNNING


__all__ = ['NodeStatus']
