"""Durable acceptance and idempotency at the SQLite boundary."""

import sqlite3
import uuid
from pathlib import Path

import pytest

from src.engine.sessions.session_store import SessionStore
from src.engine.storage.accepted_inputs import (
    AcceptedInputs,
    DeliveryConfirmation,
    DeliveryConflictError,
    ImageInput,
    InputConflictError,
    confirm_delivery,
)


def _confirm(
    store: SessionStore,
    session_id: str,
    request: str,
    attempt: str,
    run: str,
    *,
    destination: str | None = None,
) -> None:
    with store.database.transaction() as conn:
        confirm_delivery(
            conn,
            [DeliveryConfirmation(session_id, request, attempt, run)],
            destination_id=destination or session_id,
        )


def test_retry_keeps_persisted_image_identity_and_rejects_changed_payload(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request_id = str(uuid.uuid4())
    image = ImageInput(data=b'image bytes', media_type='image/png')

    accepted = store.accept_input(session, request_id, 'hello', [image], 'turn')
    retried = store.accept_input(session, request_id, 'hello', [image], 'turn')

    assert accepted == retried
    assert accepted.images[0].image_id.startswith('img_')
    assert store.load(session.id) is not None
    with pytest.raises(InputConflictError):
        store.accept_input(session, request_id, 'different', [image], 'turn')


def test_failed_acceptance_does_not_materialize_draft(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    updated_at = session.updated_at
    with pytest.raises(ValueError):
        store.accept_input(session, 'invalid', 'hello', [], 'turn')
    assert session._draft
    assert session.updated_at == updated_at
    assert store.database.value('SELECT COUNT(*) FROM sessions WHERE id = ?', (session.id,)) == 0


def test_state_transitions_and_session_scoped_identifiers(tmp_path: Path) -> None:
    from src.engine.storage.accepted_inputs import AcceptedInputs

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    first = store.draft_session()
    second = store.draft_session()
    request_id = str(uuid.uuid4())
    one = store.accept_input(first, request_id, 'first', [], 'turn')
    two = store.accept_input(second, request_id, 'second', [], 'turn')
    assert one.sequence == two.sequence == 1
    queue = AcceptedInputs(store.database)
    claimed = queue.transition(first.id, request_id, 'claimed', attempt_id='attempt', library_run_id='actual-run')
    assert claimed.attempt_id == 'attempt'
    _confirm(store, first.id, request_id, 'attempt', 'actual-run')
    delivered = queue.list(first.id)[0]
    assert delivered.state == 'delivered'
    assert delivered.library_run_id == 'actual-run'
    assert queue.transition(first.id, request_id, 'finished').state == 'finished'
    with pytest.raises(ValueError):
        queue.transition(first.id, request_id, 'claimed')
    assert queue.transition(second.id, request_id, 'interrupted').state == 'interrupted'
    store.delete(first.id)
    assert queue.list(first.id) == []
    assert len(queue.list(second.id)) == 1


def test_injected_enqueue_failure_rolls_back_draft(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    draft = store.draft_session()
    store.database.execute(
        "CREATE TRIGGER fail_accept BEFORE INSERT ON accepted_inputs BEGIN SELECT RAISE(ABORT, 'failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.accept_input(draft, str(uuid.uuid4()), 'hello', [], 'turn')
    assert draft._draft
    assert store.database.value('SELECT count(*) FROM sessions WHERE id = ?', (draft.id,)) == 0


def test_canonical_encoding_preserves_unicode_order_and_content() -> None:
    from src.engine.storage.accepted_inputs import _canonical

    first = ImageInput(b'first\x00', 'image/png')
    second = ImageInput(b'second', 'image/jpeg')
    canonical = _canonical('café \U0001f680', [first, second])
    assert canonical == _canonical('café \U0001f680', [first, second])
    assert canonical != _canonical('cafe\u0301 \U0001f680', [first, second])
    assert canonical != _canonical('café \U0001f680', [second, first])
    assert canonical != _canonical('café \U0001f680', [ImageInput(first.data, 'image/jpeg'), second])
    assert canonical != _canonical('café \U0001f680', [ImageInput(b'changed', first.media_type), second])


def test_multiple_images_retry_uses_persisted_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.storage import accepted_inputs as module

    ids = iter(['img_first', 'img_second', 'img_unwanted'])
    monkeypatch.setattr(module, 'new_image_id', lambda: next(ids))
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    images = [ImageInput(b'\x00\xff', 'image/png'), ImageInput(b'\x12\x34', 'image/jpeg')]
    accepted = store.accept_input(session, request, 'á🚀', images, 'turn')
    repeated = store.accept_input(session, request, 'á🚀', list(images), 'turn')
    assert accepted == repeated
    assert [(image.data, image.media_type, image.image_id) for image in repeated.images] == [
        (b'\x00\xff', 'image/png', 'img_first'),
        (b'\x12\x34', 'image/jpeg', 'img_second'),
    ]
    assert next(ids) == 'img_unwanted'
    for changed in (
        [images[1], images[0]],
        [ImageInput(b'other', 'image/png'), images[1]],
        [ImageInput(images[0].data, 'image/jpeg'), images[1]],
    ):
        with pytest.raises(InputConflictError):
            store.accept_input(session, request, 'á🚀', changed, 'turn')


@pytest.mark.parametrize(
    'identifier',
    [
        '',
        'invalid',
        '12345678-1234-1234-9234-123456789abc',
        '12345678-1234-4234-9234-123456789ABC',
        '12345678123442349234123456789abc',
    ],
)
def test_invalid_request_ids_do_not_persist(tmp_path: Path, identifier: str) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    draft = store.draft_session()
    with pytest.raises(ValueError):
        store.accept_input(draft, identifier, 'text', [], 'turn')
    assert draft._draft
    assert store.database.value('SELECT COUNT(*) FROM sessions WHERE id = ?', (draft.id,)) == 0


def test_transition_rollback_preserves_prior_state(tmp_path: Path) -> None:
    from src.engine.storage.accepted_inputs import AcceptedInputs

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'text', [], 'turn')
    queue = AcceptedInputs(store.database)
    with pytest.raises(RuntimeError, match='rollback'), store.database.transaction() as conn:
        queue.transition(session.id, request, 'claimed', attempt_id='attempt', conn=conn)
        raise RuntimeError('rollback')
    result = queue.list(session.id)[0]
    assert result.state == 'accepted'
    assert result.attempt_id is None


@pytest.mark.parametrize(
    ('start', 'end'),
    [('accepted', 'finished'), ('accepted', 'delivered'), ('claimed', 'finished'), ('delivered', 'claimed')],
)
def test_invalid_transitions_leave_state_unchanged(tmp_path: Path, start: str, end: str) -> None:
    from typing import cast

    from src.engine.storage.accepted_inputs import AcceptedInputs, InputState

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'text', [], 'turn')
    queue = AcceptedInputs(store.database)
    if start in ('claimed', 'delivered'):
        queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    if start == 'delivered':
        _confirm(store, session.id, request, 'attempt', 'run')
    with pytest.raises(ValueError):
        queue.transition(session.id, request, cast(InputState, end))
    assert queue.list(session.id)[0].state == start


@pytest.mark.parametrize(
    ('path', 'target'),
    [
        ((), 'claimed'),
        ((), 'stopped'),
        ((), 'interrupted'),
        (('claimed',), 'stopped'),
        (('claimed',), 'interrupted'),
        (('claimed', 'delivered'), 'finished'),
        (('claimed', 'delivered'), 'stopped'),
        (('claimed', 'delivered'), 'interrupted'),
    ],
)
def test_all_approved_transitions(tmp_path: Path, path: tuple[str, ...], target: str) -> None:
    from typing import cast

    from src.engine.storage.accepted_inputs import AcceptedInputs, InputState

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'text', [], 'turn')
    queue = AcceptedInputs(store.database)
    for state in path:
        if state == 'delivered':
            _confirm(store, session.id, request, 'attempt', 'run')
            continue
        queue.transition(session.id, request, cast(InputState, state), attempt_id='attempt', library_run_id='run')
    assert queue.transition(session.id, request, cast(InputState, target)).state == target
    if target in ('finished', 'stopped', 'interrupted'):
        with pytest.raises(ValueError):
            queue.transition(session.id, request, 'claimed')


def test_direct_transition_to_delivered_is_rejected_with_and_without_conn(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'text', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')

    with pytest.raises(ValueError, match='confirm_delivery'):
        queue.transition(session.id, request, 'delivered')
    with store.database.transaction() as conn, pytest.raises(ValueError, match='confirm_delivery'):
        queue.transition(session.id, request, 'delivered', conn=conn)

    assert queue.list(session.id)[0].state == 'claimed'
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0


def test_a_checkpoint_writes_transcript_and_delivery_together(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from src.engine.storage.database import Database, database_path

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'hello', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    session.messages = [ModelRequest(parts=[UserPromptPart('hello')])]

    result = store.save(
        session,
        confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'run')],
    )

    assert result is not None
    assert result.session_id == session.id
    assert result.recovery_session_id is None
    Database.forget(database_path(store.base_dir))
    reopened = SessionStore(cwd=tmp_path, base_dir=store.base_dir)
    loaded = reopened.load(session.id)
    assert loaded is not None and loaded.messages[0].parts[0].content == 'hello'
    fact = reopened.database.one(
        'SELECT checkpointed, destination_id, destination_session_id FROM input_deliveries '
        'WHERE session_id = ? AND request_id = ?',
        (session.id, request),
    )
    assert tuple(fact) == (1, session.id, session.id)
    assert AcceptedInputs(reopened.database).list(session.id)[0].state == 'delivered'


def test_a_checkpoint_confirms_only_the_requests_it_names(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    first, second, third = (str(uuid.uuid4()) for _ in range(3))
    for request in (first, second, third):
        store.accept_input(session, request, 'text', [], 'turn')
    queue = AcceptedInputs(store.database)
    for request in (first, second, third):
        queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    session.messages = [ModelRequest(parts=[UserPromptPart('text')])]

    store.save(
        session,
        confirmations=[
            DeliveryConfirmation(session.id, first, 'attempt', 'run'),
            DeliveryConfirmation(session.id, third, 'attempt', 'run'),
        ],
    )

    states = {item.request_id: item.state for item in queue.list(session.id)}
    assert states == {first: 'delivered', second: 'claimed', third: 'delivered'}
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 2


def test_a_callback_failure_rolls_back_the_confirmed_delivery(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'hello', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    session.messages = [ModelRequest(parts=[UserPromptPart('hello')])]

    def fail(_conn: sqlite3.Connection) -> None:
        raise RuntimeError('callback failure')

    with pytest.raises(RuntimeError, match='callback failure'):
        store.save(
            session,
            confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'run')],
            _inside_transaction=fail,
        )

    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (session.root_id,)) == 0
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0
    assert queue.list(session.id)[0].state == 'claimed'


def test_a_confirmation_cannot_target_another_session(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    first = store.create_session()
    second = store.create_session()
    request = str(uuid.uuid4())
    store.accept_input(first, request, 'text', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(first.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    second.messages = [ModelRequest(parts=[UserPromptPart('other')])]

    with pytest.raises(DeliveryConflictError, match='destination'):
        store.save(
            second,
            confirmations=[DeliveryConfirmation(first.id, request, 'attempt', 'run')],
        )

    assert queue.list(first.id)[0].state == 'claimed'
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0
    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (second.root_id,)) == 0


def test_a_confirmation_without_local_progress_is_not_discarded(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.create_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'text', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    session.messages = [ModelRequest(parts=[UserPromptPart('hello')])]
    store.save(session)
    stale = store.load(session.id)
    assert stale is not None
    session.tools_tokens = 123
    store.save(session)

    with pytest.raises(DeliveryConflictError, match='progress'):
        store.save_progress_result(
            stale,
            confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'run')],
        )

    assert queue.list(session.id)[0].state == 'claimed'
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0
    assert store.database.value('SELECT count(*) FROM recovery_links') == 0
    assert len(store.list_sessions()) == 1


def test_a_second_confirmation_is_refused_and_leaves_the_fact_intact(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'hello', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    session.messages = [ModelRequest(parts=[UserPromptPart('hello')])]
    confirmation = DeliveryConfirmation(session.id, request, 'attempt', 'run')
    store.save(session, confirmations=[confirmation])
    before = store.database.one(
        'SELECT checkpointed, destination_id, destination_session_id FROM input_deliveries '
        'WHERE session_id = ? AND request_id = ?',
        (session.id, request),
    )

    with pytest.raises(DeliveryConflictError):
        store.save(session, confirmations=[confirmation])

    after = store.database.one(
        'SELECT checkpointed, destination_id, destination_session_id FROM input_deliveries '
        'WHERE session_id = ? AND request_id = ?',
        (session.id, request),
    )
    assert tuple(after) == tuple(before) == (1, session.id, session.id)
    assert queue.list(session.id)[0].state == 'delivered'


def test_a_mismatched_confirmation_rolls_back_the_checkpoint(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'hello', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    session.messages = [ModelRequest(parts=[UserPromptPart('hello')])]

    with pytest.raises(DeliveryConflictError):
        store.save(
            session,
            confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'other-run')],
        )

    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (session.root_id,)) == 0
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0
    assert queue.list(session.id)[0].state == 'claimed'


def test_a_failure_after_the_delivery_fact_rolls_back_everything(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'hello', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')
    session.messages = [ModelRequest(parts=[UserPromptPart('hello')])]
    store.database.execute(
        'CREATE TRIGGER fail_delivery_state BEFORE UPDATE OF state ON accepted_inputs '
        "BEGIN SELECT RAISE(ABORT, 'failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.save(
            session,
            confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'run')],
        )

    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (session.root_id,)) == 0
    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0
    assert queue.list(session.id)[0].state == 'claimed'


def test_a_delivery_cannot_be_confirmed_without_a_transcript(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'hello', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt', library_run_id='run')

    with pytest.raises(ValueError, match='without a transcript'):
        store.save(
            session,
            confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'run')],
        )

    assert store.database.value('SELECT count(*) FROM input_deliveries') == 0
    assert queue.list(session.id)[0].state == 'claimed'


def test_persisted_payload_and_fingerprint_are_versioned(tmp_path: Path) -> None:
    import hashlib
    import json

    from src.engine.storage.accepted_inputs import _canonical

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    request = str(uuid.uuid4())
    images = [ImageInput(b'\x00\xff', 'image/png')]
    accepted = store.accept_input(session, request, 'é🚀', images, 'turn')
    row = store.database.one('SELECT payload, fingerprint FROM accepted_inputs WHERE session_id = ?', (session.id,))
    assert row is not None
    payload = json.loads(row['payload'])
    assert payload['version'] == 1
    assert payload['images'][0]['image_id'] == accepted.images[0].image_id
    assert row['fingerprint'] == hashlib.sha256(_canonical('é🚀', images).encode()).hexdigest()


def test_digest_collision_cannot_hide_different_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.storage import accepted_inputs as module

    class ConstantDigest:
        def hexdigest(self) -> str:
            return 'constant'

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.create_session()
    monkeypatch.setattr(module.hashlib, 'sha256', lambda data: ConstantDigest())
    request = str(uuid.uuid4())
    images = [ImageInput(b'one', 'image/png')]
    first = store.accept_input(session, request, 'same', images, 'turn')
    assert store.accept_input(session, request, 'same', images, 'turn') == first
    with pytest.raises(InputConflictError):
        store.accept_input(session, request, 'different', images, 'turn')
    with pytest.raises(InputConflictError):
        store.accept_input(session, request, 'same', [ImageInput(b'two', 'image/png')], 'turn')


def test_distinct_requests_with_identical_text_are_distinct_messages(tmp_path: Path) -> None:
    from src.engine.storage.accepted_inputs import AcceptedInputs

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    session = store.draft_session()
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    first = store.accept_input(session, first_id, 'identical', [], 'turn')
    second = store.accept_input(session, second_id, 'identical', [], 'turn')
    assert (first.request_id, first.sequence) == (first_id, 1)
    assert (second.request_id, second.sequence) == (second_id, 2)
    assert store.accept_input(session, first_id, 'identical', [], 'turn') == first
    assert len(AcceptedInputs(store.database).list(session.id)) == 2


def test_the_open_row_question_is_answered_from_a_partial_index(tmp_path: Path) -> None:
    """The runtime asks this on a timer, so the table must not be scanned.

    `accepted_inputs` only grows, and the service asks "is any row still open" every
    half second for as long as it runs. `accepted_inputs_open` is a partial index over
    exactly the three open states, so the question is a lookup. This asserts the plan
    SQLite reports for the query the runtime actually runs — `OPEN_INPUTS_SQL`, not a
    copy of it — rather than the intention behind the index.
    """
    from src.engine.storage.accepted_inputs import OPEN_INPUTS_SQL

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'db')
    plan = ' '.join(str(row['detail']) for row in store.database.query(f'EXPLAIN QUERY PLAN {OPEN_INPUTS_SQL}'))

    assert 'accepted_inputs_open' in plan, plan
    assert 'SCAN' not in plan, plan
