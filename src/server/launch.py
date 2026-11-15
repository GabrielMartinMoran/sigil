"""A service a client may talk to now, and whether this call started it."""

from __future__ import annotations

from dataclasses import dataclass

from src.server.child import Child
from src.server.endpoint import Endpoint


@dataclass(frozen=True, slots=True)
class Launch:
    """A service a client may talk to now, and whether this call started it.

    ``child`` is the process this call spawned, when it spawned one. It is ``None``
    when the service was already there, which is the common case: the second
    terminal, the second page and every later command attach instead of starting.
    """

    endpoint: Endpoint
    child: Child | None = None

    @property
    def launched(self) -> bool:
        """Whether this call started the process serving this endpoint.

        The answer is an identity check, and it happens where the decision is made:
        `_serving_child` compares the pid the publisher wrote into the endpoint file
        with the pid of the child this call spawned. Equal means this call launched
        the service. Different means another candidate won the lock and this one
        attached to its service, so nothing was launched here — a claim that matters
        to a caller that would otherwise report starting a server it did not start.
        """

        return self.child is not None
