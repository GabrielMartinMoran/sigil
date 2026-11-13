"""The hub's internal event ownership, and the wire data it must not change.

Publishing is synchronous and every queue is bounded, so the only thing this
file adds is a scope: an event may belong to one session, a subscriber may ask
for one session, and matching happens before a queue is touched. That ordering
is the contract, not an optimisation — a busy neighbour must not consume a
client's backlog or make it lag.

Ownership is internal metadata and is named `owner_session_id` at the publishing
edge: `turn_started` and `turn_finished` already send `session_id` as payload,
and publishing must keep serializing it byte-for-byte while the event itself
stays unowned. That is why the serialization checks go through `EventHub.publish`
and not only through a directly constructed `Event`.
"""

from __future__ import annotations

import asyncio

from src.server.events import Event, EventHub, Subscription

NOTHING_PENDING_SECONDS = 0.01
"""How long a queue has to withhold an event before the test calls it empty.

The check waits for the absence, so it costs this bound only when the assertion
holds; no fixed sleep stands in for the queue being drained.
"""


async def _take(subscription: Subscription, count: int) -> list[Event]:
    """Pull exactly the events a subscriber is expected to have queued."""
    events: list[Event] = []
    for _ in range(count):
        event = await subscription.next()
        assert event is not None, 'a queued event resolves without waiting for the timeout'
        events.append(event)
    return events


async def _nothing_pending(subscription: Subscription) -> bool:
    """True when the subscriber's queue stays empty for the bounded wait."""
    return await subscription.next(timeout=NOTHING_PENDING_SECONDS) is None


# --- wire compatibility -----------------------------------------------------


def test_ownership_leaves_sse_bytes_and_dict_keys_untouched() -> None:
    """Internal ownership exists for routing, so the client sees the old bytes."""
    event = Event(
        seq=7,
        type='turn',
        data={'session_id': 'payload-session', 'text': 'done'},
        session_id='internal-alpha',
    )

    assert event.to_dict() == {'seq': 7, 'type': 'turn', 'session_id': 'payload-session', 'text': 'done'}
    assert list(event.to_dict()) == ['seq', 'type', 'session_id', 'text']
    assert event.to_sse() == (
        'id: 7\nevent: turn\ndata: {"seq": 7, "type": "turn", "session_id": "payload-session", "text": "done"}\n\n'
    )
    assert event.data == {'session_id': 'payload-session', 'text': 'done'}


def test_existing_turn_payload_session_id_survives_publish() -> None:
    """The shipped publishers send `session_id` as data, not as ownership."""
    hub = EventHub()

    started = hub.publish('turn_started', session_id='session-abc')
    finished = hub.publish('turn_finished', status='ok', error=None, session_id='session-abc')

    assert started.data == {'session_id': 'session-abc'}
    assert started.session_id is None
    assert started.to_dict() == {'seq': 1, 'type': 'turn_started', 'session_id': 'session-abc'}
    assert started.to_sse() == (
        'id: 1\nevent: turn_started\ndata: {"seq": 1, "type": "turn_started", "session_id": "session-abc"}\n\n'
    )

    assert finished.data == {'status': 'ok', 'error': None, 'session_id': 'session-abc'}
    assert finished.session_id is None
    assert finished.to_dict() == {
        'seq': 2,
        'type': 'turn_finished',
        'status': 'ok',
        'error': None,
        'session_id': 'session-abc',
    }
    assert finished.to_sse() == (
        'id: 2\nevent: turn_finished\ndata: '
        '{"seq": 2, "type": "turn_finished", "status": "ok", "error": null, "session_id": "session-abc"}\n\n'
    )


def test_explicit_owner_and_payload_session_id_are_independent() -> None:
    """Only `owner_session_id` routes; a payload `session_id` stays opaque data."""
    hub = EventHub()
    alpha = hub.subscribe(session_id='alpha')
    payload_scoped = hub.subscribe(session_id='payload-beta')

    event = hub.publish('turn_started', owner_session_id='alpha', session_id='payload-beta')

    assert event.session_id == 'alpha'
    assert event.data == {'session_id': 'payload-beta'}
    assert event.to_dict() == {'seq': 1, 'type': 'turn_started', 'session_id': 'payload-beta'}

    async def go() -> tuple[list[str], bool]:
        received = await _take(alpha, 1)
        return [item.type for item in received], await _nothing_pending(payload_scoped)

    alpha_types, payload_scoped_empty = asyncio.run(go())

    assert alpha_types == ['turn_started']
    assert payload_scoped_empty is True
    assert [item.seq for item in hub.replay(session_id='alpha')] == [1]
    assert hub.replay(session_id='payload-beta') == []


def test_ownership_is_an_appended_optional_field() -> None:
    """Existing positional construction keeps its meaning."""
    event = Event(1, 'notice', {'text': 'hi'})

    assert event.session_id is None
    assert event.to_dict() == {'seq': 1, 'type': 'notice', 'text': 'hi'}


def test_matching_never_reads_the_payload() -> None:
    """A payload `session_id` is data; ownership is the field on the event."""
    service_wide = Event(seq=1, type='notice', data={'session_id': 'alpha'})
    owned = Event(seq=2, type='notice', data={'session_id': 'alpha'}, session_id='beta')

    assert service_wide.matches_session('alpha') is True
    assert owned.matches_session('alpha') is False
    assert owned.matches_session('beta') is True
    assert owned.matches_session(None) is True


# --- live delivery ----------------------------------------------------------


def test_live_delivery_isolates_sessions_and_keeps_an_unfiltered_observer() -> None:
    """Each filter sees its own session plus service-wide events, in order."""
    hub = EventHub()
    alpha = hub.subscribe(session_id='alpha')
    beta = hub.subscribe(session_id='beta')
    observer = hub.subscribe()

    hub.publish('alpha_event', owner_session_id='alpha', text='one')
    hub.publish('beta_event', owner_session_id='beta', text='two')
    hub.publish('notice', text='everyone')

    async def go() -> tuple[list[Event], list[Event], list[Event]]:
        return (
            await _take(alpha, 2),
            await _take(beta, 2),
            await _take(observer, 3),
        )

    alpha_events, beta_events, observer_events = asyncio.run(go())

    assert [event.type for event in alpha_events] == ['alpha_event', 'notice']
    assert [event.type for event in beta_events] == ['beta_event', 'notice']
    assert [event.type for event in observer_events] == ['alpha_event', 'beta_event', 'notice']
    assert [event.session_id for event in alpha_events] == ['alpha', None]
    assert asyncio.run(_nothing_pending(alpha)) is True
    assert asyncio.run(_nothing_pending(beta)) is True


def test_publish_state_forwards_ownership_without_touching_payload() -> None:
    """State changes may be addressed to a session or left service-wide."""
    hub = EventHub()

    owned = hub.publish_state('turn', owner_session_id='alpha')
    service_wide = hub.publish_state('queue')

    assert (owned.type, owned.data, owned.session_id) == ('state_changed', {'reason': 'turn'}, 'alpha')
    assert (service_wide.type, service_wide.data, service_wide.session_id) == (
        'state_changed',
        {'reason': 'queue'},
        None,
    )


# --- replay -----------------------------------------------------------------


def test_replay_filters_by_scope_and_keeps_global_cursors() -> None:
    """Gaps are other sessions' sequence numbers, not a reason to widen the filter."""
    hub = EventHub()
    first = hub.publish('a1', owner_session_id='alpha')
    second = hub.publish('b1', owner_session_id='beta')
    third = hub.publish('notice', text='everyone')
    fourth = hub.publish('a2', owner_session_id='alpha')
    fifth = hub.publish('b2', owner_session_id='beta')

    assert [event.seq for event in hub.replay(first.seq, session_id='alpha')] == [third.seq, fourth.seq]
    assert [event.type for event in hub.replay(first.seq, session_id='alpha')] == ['notice', 'a2']
    assert [event.seq for event in hub.replay(session_id='alpha')] == [first.seq, third.seq, fourth.seq]
    assert [event.seq for event in hub.replay()] == [first.seq, second.seq, third.seq, fourth.seq, fifth.seq]
    assert hub.replay(fifth.seq, session_id='alpha') == []
    assert hub.last_seq == fifth.seq == 5


def test_replay_then_live_delivery_neither_duplicates_nor_loses_matching_events() -> None:
    """The reconnect path: subscribe, replay past a global cursor, then read live."""
    hub = EventHub()
    hub.publish('a0', owner_session_id='alpha')
    hub.publish('b0', owner_session_id='beta')
    cursor = hub.last_seq
    subscription = hub.subscribe(session_id='alpha')

    async def go() -> tuple[list[str], list[str]]:
        replayed = hub.replay(0, session_id='alpha')
        hub.publish('a1', owner_session_id='alpha')
        hub.publish('b1', owner_session_id='beta')
        live = await _take(subscription, 1)
        return [event.type for event in replayed], [event.type for event in live]

    replayed, live = asyncio.run(go())

    assert replayed == ['a0']
    assert live == ['a1']
    assert cursor == 2


# --- queue pressure and overflow --------------------------------------------


def test_foreign_traffic_never_touches_a_filtered_queue() -> None:
    """A neighbour's traffic cannot fill a queue it never enters."""
    hub = EventHub(queue_size=2)
    alpha = hub.subscribe(session_id='alpha')

    for _ in range(10):
        hub.publish('beta_event', owner_session_id='beta', text='noise')

    assert asyncio.run(_nothing_pending(alpha)) is True

    hub.publish('alpha_event', owner_session_id='alpha')

    assert [event.type for event in asyncio.run(_take(alpha, 1))] == ['alpha_event']


def test_overflow_lags_only_the_subscription_that_overflowed() -> None:
    """The marker is local, scoped to that subscriber, and absent from replay."""
    hub = EventHub(queue_size=2)
    alpha = hub.subscribe(session_id='alpha')
    beta = hub.subscribe(session_id='beta')

    hub.publish('alpha_event', owner_session_id='alpha', text='one')
    hub.publish('beta_event', owner_session_id='beta', text='two')
    hub.publish('alpha_event', owner_session_id='alpha', text='three')
    hub.publish('alpha_event', owner_session_id='alpha', text='four')

    async def go() -> tuple[list[Event], list[Event]]:
        return await _take(alpha, 2), await _take(beta, 1)

    alpha_events, beta_events = asyncio.run(go())

    assert [event.type for event in alpha_events] == ['alpha_event', 'lagged']
    assert alpha_events[1].seq == 4
    assert alpha_events[1].session_id == 'alpha'
    assert alpha_events[1].data == {'reason': 'the client was too far behind'}
    assert alpha_events[1].to_sse() == (
        'id: 4\nevent: lagged\ndata: {"seq": 4, "type": "lagged", "reason": "the client was too far behind"}\n\n'
    )
    assert [event.type for event in beta_events] == ['beta_event']
    assert [event.type for event in hub.replay()] == ['alpha_event', 'beta_event', 'alpha_event', 'alpha_event']
    assert hub.last_seq == 4


# --- cleanup ----------------------------------------------------------------


def test_closing_a_subscription_releases_its_queue() -> None:
    """A closed subscription stops receiving without disturbing its neighbours."""
    hub = EventHub()
    closed = hub.subscribe(session_id='alpha')
    still_open = hub.subscribe(session_id='alpha')
    assert hub.subscriber_count == 2

    closed.close()
    hub.publish('alpha_event', owner_session_id='alpha')

    async def go() -> tuple[list[Event], bool]:
        return await _take(still_open, 1), await _nothing_pending(closed)

    received, nothing = asyncio.run(go())

    assert hub.subscriber_count == 1
    assert [event.type for event in received] == ['alpha_event']
    assert nothing is True


def test_leaving_the_context_manager_unsubscribes() -> None:
    """The `async with` form keeps its cleanup contract."""
    hub = EventHub()

    async def go() -> int:
        async with hub.subscribe(session_id='alpha'):
            assert hub.subscriber_count == 1
        return hub.subscriber_count

    assert asyncio.run(go()) == 0
