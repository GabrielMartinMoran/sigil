"""Fan-out of what happened, for clients that are not in this process.

The rule the architecture page states and this module keeps: **an event says what
changed, never the whole state.** A client that missed a range asks for the state
and gets it from the rows; the log is a notification with a sequence number, so a
reconnect can say `after_seq` and receive only what it lost.

Three properties matter here:

- **Publishing is synchronous.** Tool sinks, roadmap listeners and the turn loop
  call it from wherever they are; a subscriber that is slow must never block the
  agent, so a full queue drops its oldest event and is told it lagged instead.
- **The buffer is small and bounded.** It exists for a reconnect that took a
  second, not to keep a conversation in memory.
- **One queue per subscriber.** No locking: the event loop is the only writer.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Self

DEFAULT_BUFFER = 2000
"""Events kept for a reconnect: a few minutes of a busy turn."""

SUBSCRIBER_QUEUE = 2000
"""Per-client backlog. Past this the client is behind the conversation, not reading it."""


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, with the sequence number a client catches up from."""

    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """The wire form: `id` for the browser, `event` for dispatch, `data` for JSON."""
        payload = {'seq': self.seq, 'type': self.type, **self.data}
        return f'id: {self.seq}\nevent: {self.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'

    def to_dict(self) -> dict[str, Any]:
        return {'seq': self.seq, 'type': self.type, **self.data}


class EventHub:
    """Publishes events to every live subscriber, remembering the recent ones."""

    def __init__(self, *, buffer_size: int = DEFAULT_BUFFER, queue_size: int = SUBSCRIBER_QUEUE) -> None:
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._queue_size = queue_size
        self._subscribers: dict[int, asyncio.Queue[Event]] = {}
        self._ids = itertools.count(1)
        self._seq = 0

    # --- writing ------------------------------------------------------------

    def publish(self, type: str, **data: Any) -> Event:
        """Records an event and hands it to every subscriber. Never raises for a slow one."""
        self._seq += 1
        event = Event(seq=self._seq, type=type, data=data)
        self._buffer.append(event)
        for key, queue in list(self._subscribers.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # The client missed events: drop the oldest and leave a marker so
                # it refetches the state instead of painting a half conversation.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - raced with the reader
                    pass
                try:
                    queue.put_nowait(
                        Event(seq=self._seq, type='lagged', data={'reason': 'the client was too far behind'})
                    )
                except asyncio.QueueFull:  # pragma: no cover - still full: the next attempt wins
                    del key
        return event

    def publish_state(self, reason: str) -> Event:
        """Asks clients to re-read the state they project from the rows."""
        return self.publish('state_changed', reason=reason)

    # --- reading ------------------------------------------------------------

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def replay(self, after_seq: int = 0) -> list[Event]:
        """The events a client missed, oldest first."""
        return [event for event in self._buffer if event.seq > after_seq]

    def subscribe(self) -> Subscription:
        """A queue of events from now on, plus whatever a reconnect needs."""
        key = next(self._ids)
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[key] = queue
        return Subscription(self, key, queue)

    def _unsubscribe(self, key: int) -> None:
        self._subscribers.pop(key, None)


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


__all__ = ['DEFAULT_BUFFER', 'SUBSCRIBER_QUEUE', 'Event', 'EventHub', 'Subscription']
