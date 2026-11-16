"""The one-way request that makes the server stop after draining."""

from __future__ import annotations

from collections.abc import Callable


class GracefulStop:
    """A one-way request to leave the serving loop, bound once the loop exists.

    The application is built before the server that runs it, and the server is
    what can stop, so neither can be handed to the other directly. This is the
    indirection: the app calls `request`, whoever runs the loop calls `bind`, and
    the request is a flag rather than an exit so the loop still unwinds in order —
    lifespan shutdown, endpoint removed, runtime closed, lock released.

    A request made before a binding is kept and applied at `bind`: a grace window
    shorter than the startup is legitimate configuration, and losing the request
    would leave the process serving forever.
    """

    def __init__(self) -> None:
        self._requested = False
        self._stop: Callable[[], None] | None = None

    @property
    def requested(self) -> bool:
        """Whether a stop has been asked for, bound or not."""
        return self._requested

    def bind(self, stop: Callable[[], None]) -> None:
        """Attaches the loop's own switch, applying a request already made."""
        self._stop = stop
        if self._requested:
            stop()

    def request(self) -> None:
        """Asks the serving loop to stop. Idempotent."""
        self._requested = True
        if self._stop is not None:
            self._stop()
