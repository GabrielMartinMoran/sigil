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

A subscriber may name a session. That filter is applied before the queue, so an
event for another session never occupies this subscriber's space and cannot
make it lag. Ownership is internal metadata: the wire forms below are unchanged.
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
    """One thing that happened, with the sequence number a client catches up from.

    ``session_id`` is internal ownership, never wire data: ``to_sse`` and
    ``to_dict`` do not know it exists, and ``data`` may carry its own unrelated
    ``session_id`` field. ``None`` means the event belongs to the service.
    """

    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None

    def to_sse(self) -> str:
        """The wire form: `id` for the browser, `event` for dispatch, `data` for JSON."""
        payload = {'seq': self.seq, 'type': self.type, **self.data}
        return f'id: {self.seq}\nevent: {self.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'

    def to_dict(self) -> dict[str, Any]:
        return {'seq': self.seq, 'type': self.type, **self.data}

    def matches_session(self, session_id: str | None) -> bool:
        """Whether a consumer scoped to ``session_id`` should receive this event.

        ``None`` on the consumer side is internal all-events access. On the event
        side it means the event is service-wide, so every scope sees it. Neither
        side consults ``data``: a payload field never implies ownership.
        """
        if session_id is None or self.session_id is None:
            return True
        return self.session_id == session_id


@dataclass(frozen=True, slots=True)
class _Subscriber:
    """A queue together with the internal scope that decides what may enter it."""

    session_id: str | None
    queue: asyncio.Queue[Event]


class EventHub:
    """Publishes events to every live subscriber, remembering the recent ones."""

    def __init__(self, *, buffer_size: int = DEFAULT_BUFFER, queue_size: int = SUBSCRIBER_QUEUE) -> None:
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._queue_size = queue_size
        self._subscribers: dict[int, _Subscriber] = {}
        self._ids = itertools.count(1)
        self._seq = 0

    # --- writing ------------------------------------------------------------

    def publish(self, type: str, *, owner_session_id: str | None = None, **data: Any) -> Event:
        """Records an event and offers it to every subscriber that matches it.

        Filtering happens here, before a queue is touched: a foreign event cannot
        consume a subscriber's space or make it lag. The sequence number stays
        global, so a filtered stream has gaps a client can safely resume from.

        ``owner_session_id`` is deliberately not named ``session_id``: shipped
        producers send ``session_id`` as payload data and it must stay there.
        """
        self._seq += 1
        event = Event(seq=self._seq, type=type, data=data, session_id=owner_session_id)
        self._buffer.append(event)
        for key, subscriber in list(self._subscribers.items()):
            if event.matches_session(subscriber.session_id):
                self._deliver(key, subscriber, event)
        return event

    def publish_state(self, reason: str, *, owner_session_id: str | None = None) -> Event:
        """Asks clients to re-read the state they project from the rows."""
        return self.publish('state_changed', reason=reason, owner_session_id=owner_session_id)

    def _deliver(self, key: int, subscriber: _Subscriber, event: Event) -> None:
        """Queue one matching event, or trade the oldest for a lag marker when full.

        The marker is written straight into this queue: it asks one client to
        refetch, so it is not a domain event and never enters the replay buffer.
        """
        try:
            subscriber.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        # The client missed events: drop the oldest and leave a marker so
        # it refetches the state instead of painting a half conversation.
        try:
            subscriber.queue.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover - raced with the reader
            pass
        marker = Event(
            seq=event.seq,
            type='lagged',
            data={'reason': 'the client was too far behind'},
            session_id=subscriber.session_id,
        )
        try:
            subscriber.queue.put_nowait(marker)
        except asyncio.QueueFull:  # pragma: no cover - still full: the next attempt wins
            self._unsubscribe(key)

    # --- reading ------------------------------------------------------------

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def replay(self, after_seq: int = 0, *, session_id: str | None = None) -> list[Event]:
        """The events a client missed, oldest first, limited to its scope."""
        return [event for event in self._buffer if event.seq > after_seq and event.matches_session(session_id)]

    def subscribe(self, *, session_id: str | None = None) -> Subscription:
        """A queue of matching events from now on, plus whatever a reconnect needs."""
        key = next(self._ids)
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[key] = _Subscriber(session_id=session_id, queue=queue)
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
