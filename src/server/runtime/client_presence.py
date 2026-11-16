"""In-memory, expiring presence for independently connected clients."""

from __future__ import annotations

import time
from collections.abc import Callable


class ClientPresence:
    def __init__(self, *, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._expires: dict[str, float] = {}
        self._closed: dict[str, float] = {}
        self._requests = 0
        self._stopping = False

    def begin_request(self) -> bool:
        if self._stopping:
            return False
        self._requests += 1
        return True

    def end_request(self) -> None:
        self._requests -= 1

    def begin_shutdown(self, has_work: Callable[[], bool]) -> bool:
        if self._stopping or self._requests or self.active() or has_work():
            return False
        self._stopping = True
        return True

    def heartbeat(self, client_id: str) -> None:
        now = self._clock()
        if self._closed.get(client_id, 0) > now:
            return
        self._expires[client_id] = now + self._ttl

    def remove(self, client_id: str) -> None:
        self._expires.pop(client_id, None)
        self._closed[client_id] = self._clock() + self._ttl

    def active(self) -> bool:
        now = self._clock()
        self._expires = {key: expiry for key, expiry in self._expires.items() if expiry > now}
        self._closed = {key: expiry for key, expiry in self._closed.items() if expiry > now}
        return bool(self._expires)
