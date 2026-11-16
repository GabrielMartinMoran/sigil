"""Decides when an idle service should stop."""

from __future__ import annotations

import time
from collections.abc import Callable


class IdleShutdown:
    """Decides when a service that has had no work for a while should stop.

    Two facts keep the service alive: a turn that is running, and any queue row
    that is not terminal — in *any* session, because the service serves one
    database, not one browser. Connected event subscribers are deliberately not on
    that list: a page can reconnect to whatever service is there, so an open tab is
    not a reason for a process to outlive the work.

    A pending question needs no rule of its own. The agent asks from inside the
    turn that is waiting for the answer, so the turn is still running and the
    question is covered by the first fact; there are no interactive approvals.

    The clock and the work check are arguments, so a test advances the window
    instead of sleeping through it, and the thirty-minute decision costs nothing.
    """

    def __init__(
        self,
        has_work: Callable[[], bool],
        *,
        grace: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._has_work = has_work
        self._grace = grace
        self._clock = clock
        self._idle_since: float | None = None

    @property
    def grace(self) -> float:
        """Seconds of no work this decision waits for."""
        return self._grace

    def expired(self) -> bool:
        """Whether the service has gone ``grace`` seconds without work.

        Called repeatedly: the first look starts the window, work drops it and
        starts it again later, so a burst of short turns never adds up to a
        shutdown. A work check that raises is not proof of idleness — the exception
        propagates, and the caller decides that an unanswerable question means
        "keep serving".
        """
        now = self._clock()
        if self._has_work():
            self._idle_since = None
            return False
        if self._idle_since is None:
            self._idle_since = now
        return now - self._idle_since >= self._grace
