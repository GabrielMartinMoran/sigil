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
from src.engine.mcp.deferred import build_manager
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
from tests.test_server_parity import _busy, _composition

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


# --- the four commands that travel -------------------------------------------
#
# One test per route, against the real application, because the contract is the
# status code: `409` means the session is in the way and `400` means the request
# named something unusable. A screen branches on those two, so they are what the
# client has to get right.


def test_context_returns_the_service_projection_with_a_real_zero(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A cold session is zero, and zero is a measurement, not a placeholder."""

    async def go() -> dict[str, Any]:
        async with _served(runtime, tmp_path) as client:
            return await client.context()

    context = asyncio.run(go())

    assert context['window'] == 100_000
    assert context['used'] == 0
    assert context['percent'] == 0.0
    assert context['measured'] is None
    assert set(context['budget']) >= {'tools', 'instructions', 'available_for_history'}


def test_external_files_changes_the_services_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> tuple[dict[str, Any], bool]:
        async with _served(runtime, tmp_path) as client:
            session = await client.external_files(True)
            return session, runtime.session.allow_external_files

    session, stored = asyncio.run(go())

    assert session['external_files'] is True
    assert stored is True


def test_external_files_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    """`409`, and the reason is the service's own sentence."""

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            async with _busy(runtime):
                with pytest.raises(ServiceBusy) as caught:
                    await client.external_files(True)

            assert 'A turn is running' in str(caught.value)
            assert runtime.session.allow_external_files is False

    asyncio.run(go())


def test_effort_changes_the_services_model_and_reads_back(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> dict[str, Any]:
        async with _served(runtime, tmp_path) as client:
            model = await client.effort('high')
            assert runtime.composition.model_service.snapshot().thinking == 'high'
            return model

    model = asyncio.run(go())

    assert model['thinking'] == 'high'


def test_effort_refuses_a_level_the_model_does_not_accept(runtime: ServerRuntime, tmp_path: Path) -> None:
    """`400`: a different request would work, which is not what `409` says."""

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(RequestRejected):
                await client.effort('max')
            assert runtime.composition.model_service.snapshot().thinking != 'max'

    asyncio.run(go())


def test_effort_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            async with _busy(runtime):
                with pytest.raises(ServiceBusy) as caught:
                    await client.effort('high')
            # The wording is the model service's, not the route's: the refusal is
            # the engine's own `BusyError`, which is the point of not restating it.
            assert 'A turn is in progress' in str(caught.value)

    asyncio.run(go())


# --- MCP: the layer is fenced off from the developer's home config ------------


def _mcp_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServerRuntime:
    """One configured server and an installed layer, with `~/.sigil` fenced out.

    `config_paths` falls back to the home directory, so without `SIGIL_CONFIG` the
    list this asserts would depend on the machine the suite ran on.
    """
    config = tmp_path / '.sigil' / 'config.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'mcp_servers': {'probe': {'command': 'npx', 'prefix': ''}}}), encoding='utf-8')
    monkeypatch.setenv('SIGIL_CONFIG', str(config))
    composition = _composition(tmp_path, monkeypatch)
    composition.mcp.install(build_manager(composition.mcp_config.servers, None))
    return ServerRuntime(composition, EventHub(), require_ownership=False)


def test_mcp_servers_lists_and_toggles_through_the_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _mcp_runtime(tmp_path, monkeypatch)

    async def go() -> tuple[list[str], bool]:
        async with _served(runtime, tmp_path) as client:
            assert [row['name'] for row in await client.mcp_servers()] == ['probe']
            rows = await client.toggle_mcp('probe', False)
            return [row['name'] for row in rows], rows[0]['enabled']

    try:
        names, enabled = asyncio.run(go())
    finally:
        runtime.abort()

    assert names == ['probe']
    assert enabled is False
    assert runtime.session.mcp_overrides == {'probe': False}


def test_an_unknown_mcp_server_is_a_rejection_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _mcp_runtime(tmp_path, monkeypatch)

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(RequestRejected) as caught:
                await client.toggle_mcp('ghost', False)
            assert 'No MCP server named' in str(caught.value)

    try:
        asyncio.run(go())
    finally:
        runtime.abort()
    assert runtime.session.mcp_overrides == {}


# --- the agent and session senders -------------------------------------------


def test_select_agent_switches_the_services_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> dict[str, Any]:
        async with _served(runtime, tmp_path) as client:
            payload = await client.select_agent('reviewer')
            assert runtime.session.agent == 'reviewer'
            state = await client.state()
            assert state['session']['agent'] == 'reviewer'
            return payload

    payload = asyncio.run(go())

    assert (payload.get('agent') or {}).get('name') == 'reviewer'


def test_an_unknown_agent_is_a_rejection_and_changes_nothing(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(RequestRejected) as caught:
                await client.select_agent('ghost')
            # The listing is the service's: the sentence names what it could offer.
            assert 'Unknown agent' in str(caught.value)
            assert runtime.session.agent != 'ghost'

    asyncio.run(go())


def test_select_agent_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            async with _busy(runtime):
                with pytest.raises(ServiceBusy) as caught:
                    await client.select_agent('reviewer')
            assert 'turn' in str(caught.value).lower()
            assert runtime.session.agent != 'reviewer'

    asyncio.run(go())


def test_new_session_creates_the_services_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> dict[str, Any]:
        async with _served(runtime, tmp_path) as client:
            before = runtime.session.id
            created = await client.new_session()
            assert created['id'] != before
            assert runtime.session.id == created['id']
            listing = await client.sessions()
            # The service is the one that knows which session is open.
            assert listing['current'] == created['id']
            return created

    created = asyncio.run(go())

    assert created['short_id'].startswith(created['id'][:8])


def test_open_session_opens_a_stored_conversation(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> dict[str, Any]:
        async with _served(runtime, tmp_path) as client:
            first = runtime.session.id
            await client.send_turn(TurnRequest('first'))
            await runtime.wait()
            created = await client.new_session()
            assert runtime.session.id == created['id']
            opened = await client.open_session(first)
            assert runtime.session.id == first
            return opened

    opened = asyncio.run(go())

    assert opened['id'].startswith(opened['short_id'])


def test_opening_an_unknown_session_is_a_typed_refusal(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The route answers `409` for both, so the client reports the service's reason.

    ``/api/session/open`` maps a busy runtime and an unknown id to the same
    ``409``. That is the route's existing contract, not something this client can
    improve; what matters is that the sentence the user reads is the service's.
    """

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(ServiceBusy) as caught:
                await client.open_session('nope')
            assert 'No session' in str(caught.value)

    asyncio.run(go())


def test_new_session_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            before = runtime.session.id
            async with _busy(runtime):
                with pytest.raises(ServiceBusy) as caught:
                    await client.new_session()
            assert 'turn' in str(caught.value).lower()
            assert runtime.session.id == before

    asyncio.run(go())


# --- the model catalogue, the rollback and the fork ---------------------------
#
# The catalogue is a network call on the provider's side, so the tests inject a
# deterministic double: no credential and no socket are available here, and a real
# fetch would make the assertion depend on someone's plan. Everything else is the
# real application.


class _Catalog:
    """Stands in for `ModelCatalogService`: answers, or never does."""

    def __init__(self, models: tuple[Any, ...] = (), *, hangs: bool = False) -> None:
        self._models = models
        self._hangs = hangs

    async def models(self, account: Any, *, secret: str | None = None) -> Any:
        if self._hangs:
            await asyncio.Event().wait()
        return self._catalog()

    def cached(self, account: Any) -> Any:
        """`ModelService.select` consults the cache for the model's capability."""
        return self._catalog()

    def _catalog(self) -> Any:
        from src.engine.providers.domain.model_capability import ModelCatalog

        return ModelCatalog(models=self._models, source='remote')


def _with_catalog(runtime: ServerRuntime, catalog: Any) -> None:
    """Installs a catalogue double on the runtime's provider service."""
    runtime.composition.model_service.catalog = catalog


def _an_account(runtime: ServerRuntime) -> str:
    accounts = runtime.composition.model_service.accounts.load()
    assert accounts, 'the fixture configures one account'
    return accounts[0].alias


def test_the_catalog_route_returns_one_accounts_models(runtime: ServerRuntime, tmp_path: Path) -> None:
    from src.engine.providers.domain.model_capability import ModelCapability

    _with_catalog(
        runtime,
        _Catalog((ModelCapability(name='first/model', context_window=100_000, efforts=('low', 'high')),)),
    )

    async def go() -> dict[str, Any]:
        async with _served(runtime, tmp_path) as client:
            payload = await client.catalog_for(_an_account(runtime))
            assert payload['catalog']['source'] == 'remote'
            assert [model['name'] for model in payload['catalog']['models']] == ['first/model']
            assert payload['catalog']['models'][0]['efforts'] == ['low', 'high']
            return payload

    asyncio.run(go())


def test_a_catalog_that_never_answers_is_a_bounded_refusal(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route gives up instead of holding the request open.

    The bound is shortened here; the assertion is that the route stops waiting and
    says so, which is the behaviour a client depends on.
    """
    monkeypatch.setattr('src.server.app.CATALOG_TIMEOUT_SECONDS', 0.05)
    _with_catalog(runtime, _Catalog(hangs=True))

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(RequestRejected) as caught:
                await client.catalog_for(_an_account(runtime))
            assert 'did not answer' in str(caught.value)

    asyncio.run(go())


def test_an_unknown_account_is_a_rejection(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(RequestRejected):
                await client.catalog_for('no-such-account')

    asyncio.run(go())


def test_set_model_changes_the_services_model(runtime: ServerRuntime, tmp_path: Path) -> None:
    from src.engine.providers.domain.model_capability import ModelCapability

    _with_catalog(
        runtime, _Catalog((ModelCapability(name='second/model', context_window=200_000, efforts=('low', 'high')),))
    )

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            alias = _an_account(runtime)
            await client.set_model(alias, 'second/model', 'high')
            assert runtime.composition.model_service.snapshot().model == 'second/model'
            assert (await client.state())['model']['model'] == 'second/model'

    asyncio.run(go())


def test_set_model_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            before = runtime.composition.model_service.snapshot().model
            async with _busy(runtime):
                with pytest.raises(ServiceBusy):
                    await client.set_model(_an_account(runtime), 'second/model')
            assert runtime.composition.model_service.snapshot().model == before

    asyncio.run(go())


def test_rollback_points_and_the_restored_prompt(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The dropped turn's prompt comes back in the reply, read before truncation."""

    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            await client.send_turn(TurnRequest('please explain the queue'))
            await runtime.wait()

            listing = await client.rollback_points()
            points = listing['points']
            assert points, 'a turn with a prompt is a rollback point'
            target = points[-1]['turn_id']

            payload = await client.rollback(target, files=False)
            assert 'please explain the queue' in (payload['rollback']['prompt'] or '')
            assert payload['rollback']['turns_dropped'] >= 1
            assert (await client.rollback_points())['points'] == []

    asyncio.run(go())


def test_a_rollback_of_an_unknown_turn_is_a_typed_refusal(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            with pytest.raises(ServiceBusy):
                await client.rollback('not-a-turn', files=False)

    asyncio.run(go())


def test_fork_creates_a_session_and_destroys_nothing(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _served(runtime, tmp_path) as client:
            await client.send_turn(TurnRequest('first turn'))
            await runtime.wait()
            await client.send_turn(TurnRequest('branch from here'))
            await runtime.wait()
            before_session = runtime.session.id
            before_points = (await client.rollback_points())['points']

            # `points` is newest first: forking at the newest point keeps the turn below it.
            payload = await client.fork(before_points[0]['turn_id'])

            forked = payload['fork']
            assert forked['session_id'] != before_session
            assert forked['turns_copied'] >= 1
            # Nothing was destroyed: the open session and its points are untouched.
            assert runtime.session.id == before_session
            assert (await client.rollback_points())['points'] == before_points

    asyncio.run(go())
