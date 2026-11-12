"""Characterize the real agent delivery event and snapshot boundary."""

import asyncio
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessEventStream
from pydantic_ai.messages import (
    BinaryContent,
    EnqueuedMessagesEvent,
    ModelMessagesTypeAdapter,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from src.engine.sessions.session_store import SessionStore
from src.engine.storage.database import Database, database_path


def test_delivery_event_snapshots_and_reopened_history(tmp_path: Path) -> None:
    async def characterize() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        deliveries: list[tuple[Any, bytes]] = []
        initial = ['initial', BinaryContent(data=b'initial-image', media_type='image/png')]
        image = BinaryContent(data=b'identical-image', media_type='image/png')
        agent = Agent(TestModel(call_tools=['wait_for_tool'], custom_output_text='done'))

        @agent.tool_plain
        async def wait_for_tool() -> str:
            started.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            return 'tool result'

        async def capture(_ctx: Any, events: Any) -> None:
            async for event in events:
                if isinstance(event, EnqueuedMessagesEvent):
                    deliveries.append((event, ModelMessagesTypeAdapter.dump_json(run.all_messages())))

        async with agent.iter(initial, capabilities=[ProcessEventStream(handler=capture)]) as run:

            async def iterate() -> None:
                async for _ in run:
                    pass

            task = asyncio.create_task(iterate())
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                during_tool = ModelMessagesTypeAdapter.dump_json(run.all_messages())
                ids = [run.enqueue('same', image), run.enqueue('same', image)]
                release.set()
                await asyncio.wait_for(task, timeout=5)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        final = run.all_messages()
        assert len(set(ids)) == 2
        assert [event.enqueue_id for event, _ in deliveries] == ids
        tool_history = ModelMessagesTypeAdapter.validate_json(during_tool)
        assert len(tool_history) == 2
        assert isinstance(tool_history[0].parts[0], UserPromptPart)
        assert tool_history[0].parts[0].content[0] == 'initial'
        assert tool_history[0].parts[0].content[1].data == b'initial-image'
        assert tool_history[0].parts[0].content[1].media_type == 'image/png'
        for index, (event, snapshot) in enumerate(deliveries):
            history = ModelMessagesTypeAdapter.validate_json(snapshot)
            assert len(history) == 5
            assert [part.part_kind for message in history for part in message.parts] == [
                'user-prompt',
                'tool-call',
                'tool-return',
                'user-prompt',
                'user-prompt',
            ]
            assert len(event.messages) == 1
            event_message = ModelMessagesTypeAdapter.validate_json(
                ModelMessagesTypeAdapter.dump_json(list(event.messages))
            )[0]
            assert event_message == history[3 + index]
            for message in history[3:]:
                prompt = message.parts[0]
                assert isinstance(prompt, UserPromptPart)
                assert prompt.content[0] == 'same'
                assert prompt.content[1].data == b'identical-image'
                assert prompt.content[1].media_type == 'image/png'
        assert len(final) == 6
        assert final[-1].parts[0].part_kind == 'text'
        final_serialized = ModelMessagesTypeAdapter.dump_json(final)
        assert all(enqueue_id.encode() not in final_serialized for enqueue_id in ids)
        base = tmp_path / 'database'
        store = SessionStore(cwd=tmp_path, base_dir=base)
        session = store.create_session()
        session.messages = final
        store.save(session)
        Database.forget(database_path(base))
        reopened = SessionStore(cwd=tmp_path, base_dir=base)
        loaded = reopened.load(session.id)
        assert loaded is not None
        assert ModelMessagesTypeAdapter.dump_json(loaded.messages) == final_serialized
        Database.forget(database_path(base))

    asyncio.run(characterize())


# --- durable runtime consumer -------------------------------------------------

import uuid

import pytest
from pydantic_ai.models.function import FunctionModel

from src.engine.bootstrap import Composition
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.settings import load_settings
from src.engine.storage.accepted_inputs import AcceptedInputs, ImageInput
from src.server.events import EventHub
from src.server.runtime import ServerRuntime


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServerRuntime:
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=ProviderPaths(tmp_path / '.sigil'),
    )
    composition.meter.set_window(100_000)
    rt = ServerRuntime(composition, EventHub())
    yield rt
    rt.close()
    Database.forget(database_path(tmp_path / 'home'))


async def _wait_event(runtime: ServerRuntime, type: str, timeout: float = 5.0):
    sub = runtime.hub.subscribe()
    try:
        while True:
            event = await sub.next(timeout=timeout)
            if event is None:
                raise AssertionError(f'timed out waiting for {type}')
            if event.type == type:
                return event
    finally:
        sub.close()


def _texts(messages) -> list[str]:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    found = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    found.append(part.content)
    return found


def test_a_queued_turn_finishes_and_confirms_delivery(runtime: ServerRuntime) -> None:
    async def go():
        runtime.start()
        runtime.ensure_workers()
        result = await runtime.send('hola')
        await asyncio.wait_for(runtime.wait(), 5)
        return result

    result = asyncio.run(go())
    db = runtime.composition.store.database
    queue = AcceptedInputs(db)
    assert queue.list(runtime.session.id)[0].state == 'finished'
    fact = db.one(
        'SELECT checkpointed, destination_id FROM input_deliveries WHERE session_id = ? AND request_id = ?',
        (runtime.session.id, result['request_id']),
    )
    assert tuple(fact) == (1, runtime.session.id)
    loaded = runtime.composition.store.load(runtime.session.id)
    assert loaded is not None and 'hola' in _texts(loaded.messages)


def test_an_input_accepted_outside_the_runtime_is_consumed(runtime: ServerRuntime) -> None:
    async def go():
        runtime.start()
        runtime.ensure_workers()
        sub = runtime.hub.subscribe()
        request = str(uuid.uuid4())
        runtime.composition.store.accept_input(runtime.session, request, 'outside', [], 'turn')
        try:
            while True:
                event = await sub.next(timeout=5)
                assert event is not None, 'the consumer never picked the external input up'
                if event.type == 'turn_finished':
                    break
        finally:
            sub.close()

    asyncio.run(go())
    assert 'outside' in _texts(runtime.session.messages)


def test_restart_interrupts_open_inputs_without_executing(runtime: ServerRuntime) -> None:
    store = runtime.composition.store
    session = store.create_session()
    runtime.composition.bind_session(session)
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'old', [], 'turn')
    AcceptedInputs(store.database).transition(session.id, request, 'claimed', attempt_id='attempt')

    runtime.start()

    assert AcceptedInputs(store.database).list(session.id)[0].state == 'interrupted'
    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (session.root_id,)) == 0


def test_stop_leaves_an_undelivered_steer_stopped(runtime: ServerRuntime) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream(_messages, _info):
        started.set()
        await release.wait()
        yield 'partial'

    async def go():
        runtime.start()
        runtime.ensure_workers()
        runtime.composition.agent = Agent(FunctionModel(stream_function=stream))
        await runtime.send('first')
        await asyncio.wait_for(started.wait(), 5)
        steer = await runtime.send('steer')
        assert runtime.abort()
        release.set()
        await asyncio.wait_for(runtime.wait(), 5)
        return steer

    steer = asyncio.run(go())
    item = next(
        item
        for item in AcceptedInputs(runtime.composition.store.database).list(runtime.session.id)
        if item.request_id == steer['request_id']
    )
    assert item.state == 'stopped'
    assert 'steer' not in _texts(runtime.session.messages)


def test_an_accepted_steer_is_delivered_into_the_running_turn(runtime: ServerRuntime) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def go():
        runtime.start()
        runtime.ensure_workers()
        agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'))

        @agent.tool_plain
        async def hold() -> str:
            started.set()
            await release.wait()
            return 'held'

        runtime.composition.agent = agent
        await runtime.send('first')
        await asyncio.wait_for(started.wait(), 5)
        steer = await runtime.send('second')
        release.set()
        await asyncio.wait_for(runtime.wait(), 5)
        return steer

    steer = asyncio.run(go())
    assert 'second' in _texts(runtime.session.messages)
    item = next(
        item
        for item in AcceptedInputs(runtime.composition.store.database).list(runtime.session.id)
        if item.request_id == steer['request_id']
    )
    assert item.state == 'finished'


def test_no_confirmation_without_the_delivery_in_the_snapshot(runtime: ServerRuntime) -> None:
    runtime.start()
    store = runtime.composition.store
    session = store.create_session()
    runtime.composition.bind_session(session)
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'ghost', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt')
    runtime._attempts[request] = 'attempt'
    runtime._pending[request] = queue.list(session.id)[0]
    runtime._event_seen.add(request)

    class EmptyRun:
        def all_messages(self):
            return []

    assert runtime._confirmations(EmptyRun()) == []
    assert queue.list(session.id)[0].state == 'claimed'


def test_an_accepted_image_reaches_the_history(runtime: ServerRuntime) -> None:
    from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart

    async def go():
        runtime.start()
        runtime.ensure_workers()
        sub = runtime.hub.subscribe()
        request = str(uuid.uuid4())
        runtime.composition.store.accept_input(
            runtime.session, request, 'look', [ImageInput(b'\x89PNG', 'image/png')], 'turn'
        )
        try:
            while True:
                event = await sub.next(timeout=5)
                assert event is not None, 'the consumer never picked the image input up'
                if event.type == 'turn_finished':
                    break
        finally:
            sub.close()

    asyncio.run(go())
    parts = [
        part
        for message in runtime.session.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert any(
        isinstance(part.content, list)
        and any(isinstance(value, BinaryContent) and value.data == b'\x89PNG' for value in part.content)
        for part in parts
    )


def test_a_conflicting_final_save_recovers_with_confirmation(runtime: ServerRuntime) -> None:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    store = runtime.composition.store
    original_id = runtime.session.id

    async def go():
        runtime.start()
        runtime.ensure_workers()
        agent = Agent(TestModel(call_tools=['external_write'], custom_output_text='done'))

        @agent.tool_plain
        async def external_write() -> str:
            external = store.load(original_id)
            assert external is not None
            external.messages = [
                ModelRequest(parts=[UserPromptPart('remote question')]),
                ModelResponse(parts=[TextPart('remote answer')]),
            ]
            store.save(external)
            return 'wrote'

        runtime.composition.agent = agent
        result = await runtime.send('local question')
        await asyncio.wait_for(runtime.wait(), 5)
        return result

    result = asyncio.run(go())
    db = store.database
    link = db.one('SELECT destination_id FROM recovery_links WHERE original_session_id = ?', (original_id,))
    assert link is not None
    recovery_id = link['destination_id']
    fact = db.one(
        'SELECT checkpointed, destination_id FROM input_deliveries WHERE session_id = ? AND request_id = ?',
        (original_id, result['request_id']),
    )
    assert tuple(fact) == (1, recovery_id)
    item = next(item for item in AcceptedInputs(db).list(original_id) if item.request_id == result['request_id'])
    assert item.state == 'finished'


def test_a_steer_response_keeps_delivered_and_adds_request_id(runtime: ServerRuntime) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream(_messages, _info):
        started.set()
        await release.wait()
        yield 'answer'

    async def go():
        runtime.start()
        runtime.ensure_workers()
        runtime.composition.agent = Agent(FunctionModel(stream_function=stream))
        await runtime.send('first')
        await asyncio.wait_for(started.wait(), 5)
        result = await runtime.send('second')
        release.set()
        await asyncio.wait_for(runtime.wait(), 5)
        return result

    result = asyncio.run(go())
    assert result['accepted'] == 'steer'
    assert result['delivered'] is True
    assert isinstance(result['request_id'], str)


def test_a_late_steer_is_stopped_and_never_becomes_a_turn(runtime: ServerRuntime) -> None:
    store = runtime.composition.store
    session = store.create_session()
    runtime.composition.bind_session(session)
    request = str(uuid.uuid4())

    async def go():
        runtime.start()
        # Accepted after reconciliation, so this is the end-of-turn window and not
        # a restart: a steer that misses the seam must be stopped, not promoted.
        store.accept_input(session, request, 'late steer', [], 'steer')
        runtime.ensure_workers()
        await asyncio.wait_for(runtime.wait(), 5)

    asyncio.run(go())
    item = next(item for item in AcceptedInputs(store.database).list(session.id) if item.request_id == request)
    assert item.state == 'stopped'
    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (session.root_id,)) == 0


def test_abort_finishes_an_already_confirmed_delivery(runtime: ServerRuntime) -> None:
    from src.engine.storage.accepted_inputs import DeliveryConfirmation, confirm_delivery

    runtime.start()
    store = runtime.composition.store
    session = store.create_session()
    runtime.composition.bind_session(session)
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'confirmed', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, request, 'claimed', attempt_id='attempt')
    with store.database.transaction() as conn:
        confirm_delivery(
            conn,
            [DeliveryConfirmation(session.id, request, 'attempt', 'turn-id')],
            destination_id=session.id,
        )
    runtime._pending[request] = queue.list(session.id)[0]
    runtime._confirmed.add(request)

    runtime._stop_pending(session.id)

    assert queue.list(session.id)[0].state == 'finished'


def test_send_fails_when_the_consumer_is_dead(runtime: ServerRuntime) -> None:
    async def go():
        runtime.start()
        runtime.ensure_workers()
        runtime._consumer_error = RuntimeError('consumer died')
        with pytest.raises(RuntimeError, match='consumer stopped'):
            await runtime.send('nobody home')

    asyncio.run(go())
    assert AcceptedInputs(runtime.composition.store.database).list(runtime.session.id) == []


def test_ensure_workers_requires_ownership(runtime: ServerRuntime) -> None:
    from src.server.ownership import OwnershipError

    async def go():
        with pytest.raises(OwnershipError, match='requires the database lock'):
            runtime.ensure_workers()

    asyncio.run(go())


def test_close_releases_the_database_lock(runtime: ServerRuntime) -> None:
    from src.server.ownership import ServerOwnership

    runtime.start()
    path = runtime.composition.store.database.path
    runtime.close()

    next_owner = ServerOwnership(path)
    next_owner.acquire()
    next_owner.release()


def test_abort_before_delivery_stops_the_steer_and_keeps_it_out(runtime: ServerRuntime) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream(_messages, _info):
        started.set()
        await release.wait()
        yield 'answer'

    async def go():
        runtime.start()
        runtime.ensure_workers()
        runtime.composition.agent = Agent(FunctionModel(stream_function=stream))
        await runtime.send('first')
        await asyncio.wait_for(started.wait(), 5)
        steer = await runtime.send('steer')
        assert runtime.abort()
        release.set()
        await asyncio.wait_for(runtime.wait(), 5)
        return steer

    steer = asyncio.run(go())
    item = next(
        item
        for item in AcceptedInputs(runtime.composition.store.database).list(runtime.session.id)
        if item.request_id == steer['request_id']
    )
    assert item.state == 'stopped'
    assert 'steer' not in _texts(runtime.session.messages)


def test_consumer_death_interrupts_accepted_rows(runtime: ServerRuntime) -> None:
    store = runtime.composition.store
    session = store.create_session()
    runtime.composition.bind_session(session)
    request = str(uuid.uuid4())

    async def go():
        runtime.start()
        store.accept_input(session, request, 'orphan', [], 'turn')

        def boom(*_args, **_kwargs):
            raise RuntimeError('consumer died')

        runtime._accepted.claim_next = boom
        runtime._settled.clear()
        runtime.ensure_workers()
        with pytest.raises(RuntimeError, match='consumer died'):
            await asyncio.wait_for(runtime.wait(), 5)

    asyncio.run(go())
    item = next(item for item in AcceptedInputs(store.database).list(session.id) if item.request_id == request)
    assert item.state == 'interrupted'


def test_durable_read_separates_pending_from_delivered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from src.engine.sessions.session_store import SessionStore
    from src.engine.storage.accepted_inputs import DeliveryConfirmation
    from src.server import views

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
    session = store.create_session()
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    store.accept_input(session, first, 'delivered text', [], 'turn')
    store.accept_input(session, second, 'pending text', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, first, 'claimed', attempt_id='attempt')
    session.messages = [ModelRequest(parts=[UserPromptPart('delivered text')])]
    store.save(
        session,
        confirmations=[DeliveryConfirmation(session.id, first, 'attempt', 'turn-1')],
    )

    data = views.durable_session(store.database, session.id)

    assert [entry['request_id'] for entry in data['pending']] == [second]
    assert data['queue'][0]['delivered'] is True
    assert data['queue'][1]['delivered'] is False
    assert 'delivered text' in str(data['transcript'])
    assert 'pending text' not in str(data['transcript'])
    assert [entry['request_id'] for entry in data['pending']] == [second]


def test_durable_read_keeps_stopped_and_interrupted_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.sessions.session_store import SessionStore
    from src.server import views

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
    session = store.create_session()
    stopped, interrupted = str(uuid.uuid4()), str(uuid.uuid4())
    store.accept_input(session, stopped, 'stopped text', [], 'turn')
    store.accept_input(session, interrupted, 'interrupted text', [], 'turn')
    queue = AcceptedInputs(store.database)
    queue.transition(session.id, stopped, 'stopped')
    queue.transition(session.id, interrupted, 'interrupted')

    data = views.durable_session(store.database, session.id)

    states = {entry['request_id']: entry['state'] for entry in data['queue']}
    assert states == {stopped: 'stopped', interrupted: 'interrupted'}
    assert data['pending'] == []
    assert data['conflicted_entries'] == []


def test_durable_read_exposes_recovery_and_keeps_conflict_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from src.engine.sessions.session_store import SessionStore
    from src.engine.storage.accepted_inputs import DeliveryConfirmation
    from src.server import views

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
    session = store.create_session()
    session.messages = [ModelRequest(parts=[UserPromptPart('winner')])]
    store.save(session)
    stale = store.load(session.id)
    assert stale is not None
    request = str(uuid.uuid4())
    store.accept_input(session, request, 'local text', [], 'turn')
    AcceptedInputs(store.database).transition(session.id, request, 'claimed', attempt_id='attempt')
    session.messages = [ModelRequest(parts=[UserPromptPart('winner two')])]
    store.save(session)
    stale.messages = [ModelRequest(parts=[UserPromptPart('local text')])]
    result = store.save_progress_result(
        stale,
        confirmations=[DeliveryConfirmation(session.id, request, 'attempt', 'turn-1')],
    )
    assert result is not None and result.recovery_session_id is not None
    recovery = result.recovery_session_id

    data = views.durable_session(store.database, session.id)

    assert any(item['recovery_session_id'] == recovery and item['available'] for item in data['recoveries'])
    assert any(item['request_id'] == request for item in data['conflicted_entries'])
    assert 'local text' not in str(data['transcript'])

    store.delete(recovery)
    after = views.durable_session(store.database, session.id)
    assert any(item['recovery_session_id'] == recovery and not item['available'] for item in after['recoveries'])
    assert 'local text' not in str(after['transcript'])
