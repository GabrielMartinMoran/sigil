"""One client's queue of server events."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self

from src.server.event import Event

if TYPE_CHECKING:
    from src.server.event_hub import EventHub


class Subscription:
    """One client's queue. `async with hub.subscribe() as sub: await sub.next()`."""

    def __init__(self, hub: EventHub, key: int, queue: asyncio.Queue[Event]) -> None:
        self._hub = hub
        self._key = key
        self._queue = queue

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._hub._unsubscribe(self._key)

    async def next(self, timeout: float | None = None) -> Event | None:
        """The next event, or ``None`` when the caller should send a heartbeat."""
        try:
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None
