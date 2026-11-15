"""Domain invariants, without a model, execution tree, or database."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from src.engine.agents.tree.notifications import AgentNotification, Notifications


def notice(sequence: int = 1) -> AgentNotification:
    return AgentNotification(
        sequence=sequence,
        sender_id='sender',
        sender_name='worker',
        receiver_id='receiver',
        text='report',
        created_at=1.0,
    )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('sequence', 0),
        ('sequence', -1),
        ('sequence', True),
        ('sender_id', ''),
        ('sender_name', ' '),
        ('receiver_id', ''),
        ('text', ' \n'),
        ('acknowledged', 'false'),
        ('created_at', float('nan')),
        ('created_at', float('inf')),
        ('created_at', -1.0),
        ('created_at', True),
    ],
)
def test_should_reject_invalid_state_when_constructing_notification(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        replace(notice(), **{field: value})


def test_should_reject_duplicate_ids_instead_of_silently_overwriting_content() -> None:
    original = notice()
    with pytest.raises(ValueError, match='Duplicate'):
        Notifications([original, replace(original, text='different')])


def test_should_preserve_immutable_notice_when_ack_is_repeated() -> None:
    original = notice()
    log = Notifications([original])
    log.acknowledge('receiver', [original.id])
    acknowledged = log.for_receiver('receiver')[0]

    log.acknowledge('receiver', [original.id, original.id])

    assert log.for_receiver('receiver')[0] is acknowledged
    assert original.acknowledged is False
    with pytest.raises(FrozenInstanceError):
        original.__setattr__('text', 'changed')


def test_should_reject_malformed_record_with_context_when_restoring_log() -> None:
    records = [notice().to_dict(), {'sequence': 2, 'text': 'missing sender'}]
    with pytest.raises(ValueError, match='notification.*2'):
        Notifications.from_dict(records)


def test_should_keep_record_types_and_order_when_round_tripping() -> None:
    log = Notifications([notice(5), notice(2)])
    log.acknowledge('receiver', ['n-5'])

    restored = Notifications.from_dict([entry.to_dict() for entry in log.all()])

    assert restored.all() == log.all()
    assert [entry.sequence for entry in restored.for_receiver('receiver')] == [2, 5]
    assert isinstance(restored.all()[1].acknowledged, bool)


def test_should_preserve_sequence_when_publication_is_rejected() -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Sender:
        id: str
        name: str

    log = Notifications()
    sender = Sender(id='sender', name='worker')
    with pytest.raises(ValueError, match='text'):
        log.publish(sender, 'receiver', ' \n ')

    entry = log.publish(sender, 'receiver', 'valid')

    assert entry.id == 'n-1'
    assert log.all() == (entry,)


@pytest.mark.parametrize('invalid', [None, [], 'not a record', 12])
def test_should_report_invalid_record_shape_when_restoring(invalid: object) -> None:
    with pytest.raises(TypeError, match='notification'):
        Notifications.from_dict([invalid])


@pytest.mark.parametrize('timestamp', [0, 1, 1.5])
def test_should_restore_numeric_timestamp_without_losing_precision(timestamp: float) -> None:
    record = notice().to_dict()
    record['created_at'] = timestamp

    restored = Notifications.from_dict([record])

    assert restored.all()[0].created_at == timestamp


def test_should_protect_log_from_external_collection_mutation() -> None:
    source = [notice()]
    log = Notifications(source)
    source.clear()
    snapshot = log.all()
    log.acknowledge('receiver', ['n-1'])

    assert len(log.all()) == 1
    assert snapshot[0].acknowledged is False
    assert log.all()[0].acknowledged is True


@pytest.mark.parametrize('timestamp', [None, True, '1.0', [], {}])
def test_should_preserve_error_cause_when_stored_timestamp_is_not_numeric(timestamp: object) -> None:
    record: dict[str, object] = dict(notice().to_dict())
    record['created_at'] = timestamp

    with pytest.raises(ValueError, match='notification 1.*created_at') as captured:
        Notifications.from_dict([record])

    assert isinstance(captured.value.__cause__, TypeError)
