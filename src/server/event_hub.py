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
from collections import deque
from dataclasses import dataclass
from typing import Any

from src.server.event import Event
from src.server.subscription import Subscription

DEFAULT_BUFFER = 2000
"""Events kept for a reconnect: a few minutes of a busy turn."""

SUBSCRIBER_QUEUE = 2000
"""Per-client backlog. Past this the client is behind the conversation, not reading it."""


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
