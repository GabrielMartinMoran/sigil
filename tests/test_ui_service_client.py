"""The transport that turns the terminal into a client of the local service.

Three levels, each the cheapest one that proves what it has to prove:

- **Protocol, without I/O.** `SseDecoder` is a pure state machine, so the SSE rules
  — three fields, multi-line `data`, comments, CRLF and a character split across
  two reads — are unit tests that need no server at all.
- **The client, against the real application.** `httpx.ASGITransport` drives
  `create_app` in process, so reading the state, reading the durable session,
  sending a turn with ordered images and the typed refusals are contract tests
  with no socket, no child process and no provider.
- **Streaming, against a scripted transport.** `ASGITransport` buffers a response
  and asserts it completed, so a stream that never ends cannot go through it. The
  client takes its transport as a parameter for exactly this reason: the event
  tests hand over bytes and read them back in the same process, with no sleeps and
  no server.

The token is asserted absent from every error and from the client's `repr`: it is
the one secret this module carries, and a screen that prints a client must not
print a credential.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from src.engine.bootstrap import Composition
from src.server.app import create_app
from src.server.config import ServerConfig
from src.server.endpoint import Endpoint, EndpointPublisher
from src.server.events import EventHub
from src.server.launch import Launch, ServiceStarting
from src.server.runtime import PendingQuestion, ServerRuntime
from src.ui.service_client import (
    OutgoingImage,
    PayloadTooLarge,
    RequestRejected,
    ServiceBusy,
    ServiceClient,
    ServiceClientError,
    ServiceUnavailable,
    SseDecoder,
    StreamInterrupted,
    TurnRequest,
    Unauthorized,
)
from tests.test_server_parity import _composition

PASSWORD = 'secret-pass'
"""What the browser logs in with; the client never uses it."""

TOKEN = 'run-token-that-must-never-surface'
"""The bearer token this run publishes. The assertions look for exactly this."""


# --- the server under test ---------------------------------------------------


def _app(runtime: ServerRuntime, tmp_path: Path) -> FastAPI:
    """The real application, publishing the known token so a bearer client works."""
    publisher = EndpointPublisher(tmp_path / 'service' / 'endpoint.json', token=TOKEN)
    return create_app(runtime, ServerConfig(password=PASSWORD), dist=tmp_path / 'missing', publisher=publisher)


def _endpoint(*, token: str = TOKEN) -> Endpoint:
    """What the endpoint file would have carried. The port is never dialled here."""
    return Endpoint(host='sigil.test', port=80, pid=os.getpid(), token=token, started_at=0.0)


@asynccontextmanager
async def _served(
    runtime: ServerRuntime,
    tmp_path: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[ServiceClient]:
    """A client wired to the real app, unless the test brought its own transport."""
    chosen = transport or httpx.ASGITransport(app=_app(runtime, tmp_path), raise_app_exceptions=False)
    client = ServiceClient(_endpoint(), transport=chosen)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    return _composition(tmp_path, monkeypatch)


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    return ServerRuntime(composition, EventHub(), require_ownership=False)


# --- transports a test supplies ----------------------------------------------


class _RecordingTransport(httpx.AsyncBaseTransport):
    """The ASGI transport, plus the requests the client actually put on the wire."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._inner.handle_async_request(request)


class _Chunks(httpx.AsyncByteStream):
    """A response body read chunk by chunk, so a cut can happen anywhere."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _ScriptedTransport(httpx.AsyncBaseTransport):
    """One prepared reply per request, in order. Records what was asked."""

    def __init__(self, *replies: httpx.Response) -> None:
        self.requests: list[httpx.Request] = []
        self._replies = list(replies)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._replies.pop(0)


class _Unreachable(httpx.AsyncBaseTransport):
    """A port nothing listens on."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('Connection refused', request=request)


def _stream(chunks: list[bytes], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={'content-type': 'text/event-stream'}, stream=_Chunks(chunks))


def _json(status: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=payload)


# --- fixtures the terminal sends ---------------------------------------------


def _png(color: tuple[int, int, int] = (255, 0, 0), size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


def _image(color: tuple[int, int, int]) -> OutgoingImage:
    return OutgoingImage(media_type='image/png', data=base64.b64encode(_png(color)).decode('ascii'))


# --- the SSE protocol, without a socket --------------------------------------


def test_decoder_reads_the_three_fields_of_one_event() -> None:
    frames = SseDecoder().feed(b'id: 7\nevent: turn_started\ndata: {"seq": 7}\n\n')

    assert [(frame.id, frame.event, frame.data) for frame in frames] == [('7', 'turn_started', '{"seq": 7}')]


def test_decoder_joins_multi_line_data_and_ignores_comments() -> None:
    decoder = SseDecoder()

    assert decoder.feed(b': keep-alive\n\n') == []
    frames = decoder.feed(b'id: 1\nevent: note\ndata: first\ndata: second\n\n')

    assert [frame.data for frame in frames] == ['first\nsecond']


def test_decoder_holds_a_partial_event_until_its_blank_line() -> None:
    decoder = SseDecoder()

    assert decoder.feed(b'id: 2\nevent: turn_') == []
    frames = decoder.feed(b'finished\ndata: {"seq": 2}\n\n')

    assert [(frame.id, frame.event, frame.data) for frame in frames] == [('2', 'turn_finished', '{"seq": 2}')]


def test_decoder_discards_an_event_cut_before_its_terminator() -> None:
    frames = SseDecoder().feed(b'id: 1\nevent: a\ndata: one\n\nid: 2\nevent: b\ndata: two\n')

    assert [(frame.id, frame.data) for frame in frames] == [('1', 'one')]


def test_decoder_reads_crlf_and_a_character_split_across_two_reads() -> None:
    decoder = SseDecoder()

    assert decoder.feed(b'id: 3\r\nevent: delta\r\ndata: {"text": "caf\xc3') == []
    frames = decoder.feed(b'\xa9"}\r\n\r\n')

    assert [json.loads(frame.data) for frame in frames] == [{'text': 'café'}]


# --- the reads the screen needs ----------------------------------------------


def test_state_returns_the_projection_the_screen_paints(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            state = await client.state()

            assert state['session']['id'] == runtime.session.id
            assert state['busy'] is False
            assert state['seq'] == runtime.hub.last_seq

    asyncio.run(go())


def test_durable_returns_the_transcript_and_the_queue(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The durable read is of the rows, so the session has to be a row first."""

    async def go() -> None:
        session = runtime.composition.store.create_session()
        runtime.composition.bind_session(session)
        async with _served(runtime, tmp_path) as client:
            payload = await client.durable()

            assert payload['session']['id'] == session.id
            assert payload['queue'] == []
            assert payload['pending'] == []

    asyncio.run(go())


# --- the turn ---------------------------------------------------------------


def test_a_turn_carries_text_ordered_images_and_a_client_request_id(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The order is the identity of the images, so it is asserted on the wire."""

    async def go() -> None:
        recording = _RecordingTransport(httpx.ASGITransport(app=_app(runtime, tmp_path), raise_app_exceptions=False))
        async with _served(runtime, tmp_path, transport=recording) as client:
            first, second = _image((255, 0, 0)), _image((0, 0, 255))
            message = TurnRequest('compare [Image #1] and [Image #2]', images=(first, second))

            assert uuid.UUID(message.request_id).version == 4
            response = await client.send_turn(message)
            assert response['request_id'] == message.request_id

            sent = json.loads(recording.requests[-1].content)
            assert sent['text'] == message.text
            assert sent['request_id'] == message.request_id
            assert [image['data'] for image in sent['images']] == [first.data, second.data]

            queue = (await client.durable())['queue']
            assert [(row['request_id'], row['image_count']) for row in queue] == [(message.request_id, 2)]

            await asyncio.wait_for(runtime.wait(), 5)

    asyncio.run(go())


def test_a_retry_reuses_the_request_id_and_leaves_one_row(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            message = TurnRequest('look')

            first = await client.send_turn(message)
            retry = await client.send_turn(message)

            assert first['request_id'] == retry['request_id'] == message.request_id
            assert retry['duplicate'] is True
            assert len((await client.durable())['queue']) == 1

            await asyncio.wait_for(runtime.wait(), 5)

    asyncio.run(go())


def test_abort_reports_whether_a_turn_was_stopped(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            assert await client.abort() is False

            pending = asyncio.Event()
            runtime._task = asyncio.create_task(pending.wait())
            try:
                assert await client.abort() is True
            finally:
                task = runtime._task
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                runtime._task = None

    asyncio.run(go())


def test_answer_resolves_the_question_the_service_is_holding(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        runtime._pending_question = PendingQuestion(id='q-1', questions=[])
        async with _served(runtime, tmp_path) as client:
            resolved = await client.answer('q-1', [{'question': 'ok?', 'answer': 'yes', 'custom': False}])

            assert resolved is True
            assert runtime._pending_question is None
            assert await client.answer('q-1') is False

    asyncio.run(go())


# --- the refusals, each with a name ------------------------------------------


def test_a_wrong_token_is_unauthorized(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        client = ServiceClient(
            _endpoint(token='not-the-run-token'),
            transport=httpx.ASGITransport(app=_app(runtime, tmp_path), raise_app_exceptions=False),
        )
        try:
            with pytest.raises(Unauthorized) as caught:
                await client.state()
        finally:
            await client.aclose()

        assert caught.value.code == 'unauthorized'
        assert 'not-the-run-token' not in str(caught.value)

    asyncio.run(go())


def test_a_busy_session_is_a_typed_conflict() -> None:
    """`409` is the session's state, not the request's: repeating it later works."""

    async def go() -> None:
        client = ServiceClient(
            _endpoint(),
            transport=_ScriptedTransport(
                _json(409, {'error': 'A turn is running: finish or stop it before you send a message.'})
            ),
        )
        try:
            with pytest.raises(ServiceBusy) as caught:
                await client.send_turn(TurnRequest('hello'))
        finally:
            await client.aclose()

        assert caught.value.code == 'busy'
        assert 'A turn is running' in str(caught.value)

    asyncio.run(go())


def test_a_rejected_turn_is_a_typed_refusal(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(RequestRejected) as caught:
                await client.send_turn(TurnRequest('   '))

        assert caught.value.code == 'rejected'
        assert 'empty' in str(caught.value).lower()

    asyncio.run(go())


def test_a_body_over_the_ceiling_is_typed_too_large() -> None:
    async def go() -> None:
        client = ServiceClient(
            _endpoint(),
            transport=_ScriptedTransport(_json(413, {'error': 'The request body is larger than 64 MiB.'})),
        )
        try:
            with pytest.raises(PayloadTooLarge) as caught:
                await client.send_turn(TurnRequest('a picture too far'))
        finally:
            await client.aclose()

        assert caught.value.code == 'too_large'
        assert 'larger than 64 MiB' in str(caught.value)

    asyncio.run(go())


def test_an_unreachable_port_is_a_typed_unavailable() -> None:
    async def go() -> None:
        client = ServiceClient(_endpoint(), transport=_Unreachable())
        try:
            with pytest.raises(ServiceUnavailable) as caught:
                await client.state()
        finally:
            await client.aclose()

        assert caught.value.code == 'service_unavailable'
        assert 'sigil.test' in str(caught.value)

    asyncio.run(go())


def test_a_failed_launch_keeps_the_launchers_name_for_the_reason(tmp_path: Path) -> None:
    async def go() -> None:
        def launch(**_: object) -> Launch:
            raise ServiceStarting('Another process owns the lock and published no endpoint within 20.0s.')

        with pytest.raises(ServiceUnavailable) as caught:
            await ServiceClient.connect(database=tmp_path / 'sigil.db', launch=launch)

        assert caught.value.code == 'service_unavailable'
        assert caught.value.launch_code == 'service_starting'
        assert 'Another process owns the lock' in str(caught.value)

    asyncio.run(go())


def test_connect_attaches_to_what_the_launcher_returns(tmp_path: Path, runtime: ServerRuntime) -> None:
    async def go() -> None:
        seen: dict[str, Any] = {}

        def launch(**kwargs: Any) -> Launch:
            seen.update(kwargs)
            seen['thread'] = threading.get_ident()
            return Launch(endpoint=_endpoint())

        transport = httpx.ASGITransport(app=_app(runtime, tmp_path), raise_app_exceptions=False)
        client = await ServiceClient.connect(database=tmp_path / 'sigil.db', launch=launch, transport=transport)
        try:
            assert seen['database'] == tmp_path / 'sigil.db'
            # The launcher waits seconds and spawns a process: it must not do that
            # on the loop the screen is drawing from.
            assert seen['thread'] != threading.get_ident()
            assert (await client.state())['session']['id'] == runtime.session.id
        finally:
            await client.aclose()

    asyncio.run(go())


# --- the event stream --------------------------------------------------------


# --- recovering from a restart ----------------------------------------------
#
# Two rules, both cheap enough to prove without a terminal: a refresh that finds the
# address it already has must not rebuild anything, and a refresh that does find a
# new one must rebuild over the transport the client was given. The second is the
# one that matters in production and in tests: a rebuild that dialled the network
# would look like a recovery in a test that handed over a double, and would fail in
# the test file that always passes one.


def test_a_refresh_that_finds_the_same_endpoint_rebuilds_nothing(runtime: ServerRuntime, tmp_path: Path) -> None:
    """An unchanged endpoint is not a recovery: same pool, same credential."""

    async def go() -> None:
        recorded = _RecordingTransport(httpx.ASGITransport(app=_app(runtime, tmp_path), raise_app_exceptions=False))
        asked = 0

        def launch(**_: Any) -> Launch:
            nonlocal asked
            asked += 1
            return Launch(endpoint=_endpoint())

        client = ServiceClient(_endpoint(), transport=recorded, launch=launch)
        try:
            await client.state()
            assert await client.refresh_endpoint() is False, 'the address did not change'
            assert asked == 1, 'the launcher was asked exactly once'
            await client.state()
            assert recorded.requests[-1].headers['authorization'] == f'Bearer {TOKEN}', 'the token survived'
        finally:
            await client.aclose()

    asyncio.run(go())


def test_a_refresh_that_finds_another_endpoint_keeps_the_transport_it_was_given() -> None:
    """The new run's token is used, over the transport this client already holds.

    The scripted transport answers exactly one request: a client that rebuilt itself
    onto a network transport would fail here instead of passing on a different port,
    which is the whole reason the transport is preserved rather than re-created.
    """

    async def go() -> None:
        scripted = _ScriptedTransport(_json(200, {'session': {'id': 'after-the-restart'}}))
        moved = _endpoint(token='another-run')
        client = ServiceClient(_endpoint(), transport=scripted, launch=lambda **_: Launch(endpoint=moved))
        try:
            assert await client.refresh_endpoint() is True, 'a new token is a new endpoint'
            assert await client.state() == {'session': {'id': 'after-the-restart'}}
            request = scripted.requests[-1]
            assert request.url.host == 'sigil.test'
            assert request.url.path == '/api/state'
            assert request.headers['authorization'] == 'Bearer another-run', 'the new run publishes a new token'
        finally:
            await client.aclose()

    asyncio.run(go())


def test_the_event_stream_turns_the_wire_into_typed_events() -> None:
    async def go() -> None:
        transport = _ScriptedTransport(
            _stream([b'id: 1\nevent: turn_started\ndata: {"seq": 1, "type": "turn_started", "turn": "t"}\n\n'])
        )
        client = ServiceClient(_endpoint(), transport=transport)
        try:
            events = client.events()
            event = await asyncio.wait_for(anext(events), 5)
            await events.aclose()
        finally:
            await client.aclose()

        assert (event.id, event.type) == (1, 'turn_started')
        assert event.data['turn'] == 't'
        request = transport.requests[0]
        assert request.headers['authorization'] == f'Bearer {TOKEN}'
        assert TOKEN not in str(request.url)

    asyncio.run(go())


def test_the_stream_resumes_from_the_last_id_it_read() -> None:
    """Closing is clean, and the cursor is what a reconnect asks for."""

    async def go() -> None:
        first = (
            b'id: 1\nevent: a\ndata: {"seq": 1}\n\n'
            b'id: 2\nevent: b\ndata: {"seq": 2}\n\n'
            b'id: 3\nevent: c\ndata: {"seq": 3}\n\n'
        )
        resumed = b'id: 3\nevent: c\ndata: {"seq": 3}\n\n'
        transport = _ScriptedTransport(_stream([first]), _stream([resumed]))
        client = ServiceClient(_endpoint(), transport=transport)
        try:
            events = client.events()
            assert (await asyncio.wait_for(anext(events), 5)).id == 1
            assert (await asyncio.wait_for(anext(events), 5)).id == 2
            await events.aclose()

            again = client.events(after=2)
            replayed = await asyncio.wait_for(anext(again), 5)
            await again.aclose()
        finally:
            await client.aclose()

        assert replayed.id == 3
        resume = transport.requests[1]
        assert resume.headers['last-event-id'] == '2'
        assert resume.url.params['after'] == '2'

    asyncio.run(go())


def test_a_stream_cut_mid_event_is_typed_and_carries_the_cursor() -> None:
    async def go() -> None:
        transport = _ScriptedTransport(_stream([b'id: 1\nevent: a\ndata: {"seq": 1}\n\nid: 2\nevent: b\ndata: {"se']))
        client = ServiceClient(_endpoint(), transport=transport)
        try:
            events = client.events()
            assert (await asyncio.wait_for(anext(events), 5)).id == 1

            with pytest.raises(StreamInterrupted) as caught:
                await asyncio.wait_for(anext(events), 5)
        finally:
            await client.aclose()

        assert caught.value.code == 'stream_interrupted'
        assert caught.value.last_event_id == 1

    asyncio.run(go())


def test_the_token_never_appears_in_a_repr_or_in_an_error() -> None:
    """A service that echoes the credential must not become a leak in this client."""

    async def go() -> None:
        client = ServiceClient(
            _endpoint(),
            transport=_ScriptedTransport(
                _json(401, {'error': f'the token {TOKEN} is not the one this service published'}),
                _json(400, {'error': f'a rejected body that quotes the token {TOKEN}'}),
            ),
        )
        try:
            assert TOKEN not in repr(client)
            calls = (client.state, lambda: client.send_turn(TurnRequest('x')))
            for call in calls:
                with pytest.raises(ServiceClientError) as caught:
                    await call()
                assert TOKEN not in str(caught.value)
                assert TOKEN not in repr(caught.value)
        finally:
            await client.aclose()

    asyncio.run(go())
