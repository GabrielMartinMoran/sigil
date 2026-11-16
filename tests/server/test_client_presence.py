"""Client leases expire independently and can be renewed after expiry."""

from src.server.runtime.client_presence import ClientPresence


def test_heartbeat_registers_renews_and_restores_expired_client() -> None:
    now = [0.0]
    presence = ClientPresence(ttl=60, clock=lambda: now[0])
    presence.heartbeat('one')
    now[0] = 59
    presence.heartbeat('one')
    now[0] = 60
    assert presence.active()
    now[0] = 119
    assert not presence.active()
    presence.heartbeat('one')
    assert presence.active()


def test_removing_one_client_keeps_other_clients() -> None:
    presence = ClientPresence(ttl=60)
    presence.heartbeat('one')
    presence.heartbeat('two')
    presence.remove('one')
    presence.remove('one')
    assert presence.active()
    presence.remove('two')
    assert not presence.active()


def test_admission_and_shutdown_are_exclusive() -> None:
    presence = ClientPresence(ttl=60)
    assert presence.begin_request()
    assert not presence.begin_shutdown(lambda: False)
    presence.end_request()
    presence.heartbeat('one')
    assert not presence.begin_shutdown(lambda: False)
    presence.remove('one')
    assert not presence.begin_shutdown(lambda: True)
    assert presence.begin_shutdown(lambda: False)
    assert not presence.begin_request()


def test_late_heartbeat_does_not_reverse_explicit_close() -> None:
    now = [0.0]
    presence = ClientPresence(ttl=60, clock=lambda: now[0])
    presence.heartbeat('one')
    presence.remove('one')
    presence.heartbeat('one')
    assert not presence.active()
    now[0] = 60
    presence.heartbeat('one')
    assert presence.active()
