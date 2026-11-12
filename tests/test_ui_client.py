"""The terminal's conversation, driven by the real service.

Three levels, cheapest first:

- **Pure decisions, as unit tests.** The request a retry reuses (`pending_request`)
  and the sentence a refusal becomes (`failure_notice`) are functions, so the retry
  identity and the five failures a screen has to explain are proven without a
  terminal, a service or an event loop.
- **The wiring, against the real application.** `create_app` and a real
  `ServerRuntime` answer every request this file makes. The only substitution is on
  `/events`: `httpx.ASGITransport` waits for the application to finish and an event
  stream finishes when the test cuts it, so those two connections are served from
  the hub the application publishes into. The bytes are the service's, the replay
  and the sequence numbers are the service's, and no socket is opened.
- **The terminal itself.** `SigilApp.run_test()` types into the real composer with
  the real service behind it: the message reaches the durable queue, the deltas
  paint, a second message steers the running turn, `Esc` twice stops it, a question
  is answered over HTTP, an image travels with its marker, and a cut stream says so
  and re-reads the state.

Waiting is always for an observable fact (a row, a rendered line), never a fixed
sleep, so the file is deterministic under eight workers.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic_ai.models.test import TestModel

from src.engine.attachments.images import prepare_image
from src.engine.bootstrap import Composition
from src.engine.commands import CommandOwner, owned_by
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_option import QuestionOption
from src.engine.mcp.deferred import build_manager
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.sessions.session_store import Session
from src.engine.settings import load_settings
from src.server.app import create_app
from src.server.config import ServerConfig
from src.server.endpoint import Endpoint, EndpointPublisher
from src.server.events import Event, EventHub
from src.server.launch import Launch
from src.server.runtime import ServerRuntime
from src.ui.app import SigilApp
from src.ui.command_transport import TRANSPORT, TransportState
from src.ui.commands_mixin import render_service_context
from src.ui.service_client import (
    OutgoingImage,
    PayloadTooLarge,
    RequestRejected,
    ServiceBusy,
    ServiceClient,
    ServiceUnavailable,
    TurnRequest,
    Unauthorized,
)
from src.ui.turn_mixin import failure_notice, pending_request
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.mcp_screen import MCPScreen
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar
from tests.test_agents import write_profile
from tests.test_provider_service import _add, _service
from tests.test_server_parity import _busy

PASSWORD = 'secret-pass'
"""What the browser logs in with; the terminal uses the run token instead."""

TOKEN = 'run-token-that-must-never-surface'
"""The bearer token the service publishes. The client is built with exactly this."""

SERVICE = Endpoint(host='sigil.test', port=80, pid=0, token=TOKEN, started_at=0.0)
"""The published endpoint every terminal in this file attaches to.

It is a constant because "two clients on one service" is the same address twice: a
second terminal differs in its `ServiceClient`, not in where the service lives.
"""

# --- the service under test --------------------------------------------------


def _composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    call_tools: tuple[str, ...] = (),
    mcp_servers: dict[str, Any] | None = None,
) -> Composition:
    """A real composition with one configured account and a scripted model.

    The account is injected by the provider test's own helper, so nothing probes a
    network, and the model is `TestModel`: the behaviour under test is the wiring
    between terminal, route, runtime and engine, not a provider.

    The MCP config is pinned to this test's directory, `mcp_servers` or empty.
    `config_paths()` otherwise falls back to `~/.sigil/config.json`, and the
    serving runtime now installs the layer on the first turn: unpinned, a test
    would start whatever servers the machine happens to have configured.
    """
    config = tmp_path / '.sigil' / 'config.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'mcp_servers': mcp_servers or {}}), encoding='utf-8')
    monkeypatch.setenv('SIGIL_CONFIG', str(config))
    service = _service(tmp_path)
    _add(service)
    monkeypatch.setattr('src.engine.bootstrap.build_service', lambda *args, **kwargs: service)
    write_profile(tmp_path / '.sigil' / 'agents', 'reviewer', description='Reviews code.')
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=TestModel(custom_output_text='done', call_tools=list(call_tools)),
        provider_paths=ProviderPaths(tmp_path / 'providers'),
    )
    composition.meter.set_window(100_000)
    return composition


class _HubStream(httpx.AsyncByteStream):
    """One `/events` body, written by the hub the application publishes into."""

    def __init__(self, hub: EventHub, after: int, *, stop_after_replay: bool = False) -> None:
        self._hub = hub
        self._after = after
        self._stop_after_replay = stop_after_replay

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async with self._hub.subscribe() as subscription:
            for event in self._hub.replay(self._after):
                yield event.to_sse().encode()
            if self._stop_after_replay:
                return
            while True:
                event = await subscription.next()
                yield event.to_sse().encode()


class _ServiceTransport(httpx.AsyncBaseTransport):
    """The real application for every request, the real hub for `/events`.

    Why two paths and not one. `httpx.ASGITransport` calls the ASGI application and
    returns its response only once the application has finished: it collects the
    body. `GET /events` is a response that does not finish — it is a stream that
    stays open for the life of the terminal — so a test that sent it through the ASGI
    transport would wait for a turn, and then for the next one, and never see a byte.
    The existing client tests already hit this: they use a scripted transport for
    streaming and the ASGI transport for everything else.

    So the two are split, and the split is deliberately narrow:

    - Every other route — `/api/state`, `/api/turn`, `/api/answer` — goes through
      `httpx.ASGITransport`, which means the same application, the same middleware,
      the same bearer check and the same routes production has.
    - `/events` is served from `runtime.hub` — the very `EventHub` the application
      publishes into — using the service's own `Event.to_sse()` encoder, its own
      `replay(after)` for the resume and its own sequence numbers. The bytes are the
      service's; only the read side is a test double, and it is the side that has to
      be cuttable for the "stream broke" case to be testable at all.
    """

    def __init__(self, app: Any, hub: EventHub, *, token: str = TOKEN) -> None:
        self._inner = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._hub = hub
        self._token = token
        self.streams = 0
        self.cursors: list[int] = []
        self.cut_after_replay = False
        self.durable_reads = 0
        """How many times a screen asked for the durable rows.

        The transcript a watching terminal paints after another one typed is read
        from `/api/session/durable`, never invented locally, so this counter is the
        mechanism a test watches: if the watcher painted the message without asking
        the service, the read would not happen and the number would not move.
        """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/session/durable':
            self.durable_reads += 1
        if request.url.path != '/events':
            return await self._inner.handle_async_request(request)
        self.streams += 1
        self.cursors.append(_after(request))
        if request.headers.get('authorization') != f'Bearer {self._token}':
            return httpx.Response(401, json={'error': 'This client is not signed in to this run.'})
        cut, self.cut_after_replay = self.cut_after_replay, False
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            stream=_HubStream(self._hub, _after(request), stop_after_replay=cut),
        )


def _after(request: httpx.Request) -> int:
    """The cursor the client asked to resume from."""
    raw = request.headers.get('last-event-id') or request.url.params.get('after') or '0'
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@asynccontextmanager
async def _service_under_test(
    composition: Composition, tmp_path: Path
) -> AsyncIterator[tuple[ServerRuntime, _ServiceTransport, ServiceClient]]:
    """A runtime, the real app over it, and a client wired to that app."""
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    app = create_app(
        runtime,
        ServerConfig(password=PASSWORD),
        dist=tmp_path / 'missing',
        publisher=EndpointPublisher(tmp_path / 'service' / 'endpoint.json', token=TOKEN),
    )
    transport = _ServiceTransport(app, runtime.hub)
    client = ServiceClient(SERVICE, transport=transport)
    try:
        yield runtime, transport, client
    finally:
        await client.aclose()
        runtime.abort()
        await runtime.wait()
        runtime.close()


def _session(runtime: ServerRuntime) -> Session:
    """The conversation the terminal paints, as the projection it can read.

    The terminal does not open the store: the row belongs to the service, and what
    the screen needs is what `/api/state` projects.
    """
    source = runtime.session
    return Session(id=source.id, cwd=source.cwd, title=source.title)


def _terminal(client: ServiceClient, session: Session) -> SigilApp:
    """The production shape of the app: a service client and nothing to execute.

    The reconnect delay is zeroed so a cut stream is re-followed in the same pause
    the test is already waiting on.
    """
    app = SigilApp(client=client, session=session, settings=load_settings())
    app._service_reconnect_delay = 0.0
    return app


# --- reading the screen ------------------------------------------------------


async def _until(pilot: Any, predicate: Callable[[], bool], *, what: str, attempts: int = 600) -> None:
    """Pauses the app until the predicate holds, and fails naming what never happened."""
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError(f'the terminal never showed {what}')


def _block_text(widget: Any) -> str:
    """The text of one transcript block, read the way the block keeps it.

    A notice is a `Static` holding a `Text`, so its renderable stringifies to what it
    says. An `AssistantBlock` is not: it accumulates the answer in `_text` and paints a
    Markdown renderable whose `str()` is a repr. Reading the attribute is reading
    exactly what the screen paints — the assertion does not get weaker, it stops
    measuring `repr()`.
    """
    answer = getattr(widget, '_text', None)
    reasoning = getattr(widget, '_thinking', None)
    if isinstance(answer, str) or isinstance(reasoning, str):
        return '\n'.join(part for part in (reasoning or '', answer or '') if part)
    return str(widget.render())


def _transcript(app: SigilApp) -> str:
    return '\n'.join(_block_text(widget) for widget in app.query_one(ChatLog).children)


def _status(app: SigilApp) -> str:
    return str(app.query_one(StatusBar).render())


def _inputs(runtime: ServerRuntime) -> list[dict[str, Any]]:
    """Every durable input of the session, in the order the service accepted them."""
    rows = runtime.composition.store.database.execute(
        'SELECT kind, state, payload FROM accepted_inputs WHERE session_id = ? ORDER BY sequence',
        (runtime.session.id,),
    ).fetchall()
    return [{'kind': row['kind'], 'state': row['state'], 'payload': json.loads(row['payload'])} for row in rows]


def _png(color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(buffer, format='PNG')
    return buffer.getvalue()


def _question() -> QuestionItem:
    return QuestionItem(
        question='Which way?',
        options=[
            QuestionOption(label='Recommended', description='the short one', recommended=True),
            QuestionOption(label='Other way'),
        ],
    )


# --- the two sentences a failure becomes, as functions -----------------------


def test_a_retry_of_the_same_send_reuses_its_request_id_and_a_new_message_does_not() -> None:
    """The queue deduplicates by request id, so a retry must be the same request.

    This is the whole retry contract: the id belongs to the content, not to the
    attempt. Sending the same text and images again is the same message; changing
    either is a different one, and giving them one id would make the service drop
    the second.
    """
    images = (OutgoingImage(media_type='image/png', data='AAAA'),)
    first = pending_request(None, 'hello', images)

    assert pending_request(first, 'hello', images) is first
    assert pending_request(first, 'hello', ()) is not first
    assert pending_request(first, 'other', images) is not first
    assert pending_request(first, 'hello', (images[0], images[0])) is not first
    assert pending_request(first, 'hello', images).request_id == first.request_id


def test_every_refusal_is_a_sentence_that_names_the_way_out() -> None:
    """No traceback, and no dead end: each failure says what to do about it."""
    # The service's own busy sentence: a running turn is the reason, and the way out
    # is stopping it.
    busy = failure_notice(ServiceBusy('A turn is running: finish or stop it before you change the model.'))
    assert 'Esc twice' in busy
    assert 'prompt' in busy

    # A `409` that is not about a running turn keeps the service's words instead of
    # borrowing the busy sentence: claiming the agent is working would be a guess.
    unreachable = failure_notice(ServiceBusy('No session abc123.'))
    assert 'No session abc123.' in unreachable
    assert 'Esc twice' not in unreachable
    assert 'still working' not in unreachable

    refused = failure_notice(RequestRejected('The message is empty.'))
    assert 'The message is empty.' in refused

    too_large = failure_notice(PayloadTooLarge('over the ceiling'))
    assert 'remove an image' in too_large.lower()

    unauthorized = failure_notice(Unauthorized('the run token was refused'))
    assert 'Restart Sigil' in unauthorized

    down = failure_notice(ServiceUnavailable('No Sigil service answered at 127.0.0.1:9: refused'))
    assert 'sigil server' in down
    assert 'traceback' not in down.lower()


# --- fixtures: a composition is built off the loop ---------------------------------
#
# `Composition` is built with `_add`, which calls `asyncio.run` to store the account.
# That is a fact about the composition helper, not about this cut, so the fixtures do
# it before the test's own loop exists. Building it inside `inner()` would nest an
# event loop inside an event loop and fail for a reason that has nothing to do with
# the terminal.


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    return _composition(tmp_path, monkeypatch)


@pytest.fixture
def blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Composition, asyncio.Event]:
    """A composition whose one tool waits, so a turn can be steered and then stopped."""
    release = asyncio.Event()
    composition = _composition(tmp_path, monkeypatch, call_tools=('hold',))

    @composition.agent.tool_plain
    async def hold() -> str:
        await release.wait()
        return 'held'

    return composition, release


# --- the conversation, through the terminal ---------------------------------


def test_the_first_stream_starts_after_the_state_it_already_read(composition: Composition, tmp_path: Path) -> None:
    """The state is the snapshot; the stream continues it.

    The service remembers recent events for a reconnection. A terminal that asked for
    them starting at zero would be handed a transcript the state had just described —
    the same notice painted twice. So the first connection starts after the sequence
    number the state was taken at, and only a reconnection uses the last event applied.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, client):
            runtime.hub.publish('notice', text='before the terminal attached', tone='warn')
            assert runtime.hub.last_seq >= 1
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: bool(transport.cursors), what='a first stream')
                assert transport.cursors[0] == runtime.hub.last_seq
                assert 'before the terminal attached' not in _transcript(app)

    asyncio.run(inner())


def test_a_message_reaches_the_durable_queue_and_the_stream_paints_the_answer(
    composition: Composition, tmp_path: Path
) -> None:
    """The one journey this cut is: type, Enter, the service runs it, the screen follows."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')

                await _until(pilot, lambda: 'done' in _transcript(app), what='the answer')
                await _until(pilot, lambda: not app._turn_active, what='the end of the turn')

                assert [row['kind'] for row in _inputs(runtime)] == ['turn']
                assert app._service_state is not None, 'a finished turn re-reads the state'
                assert 'hi' in _transcript(app), 'the message the user typed is on screen'

    asyncio.run(inner())


def test_a_message_typed_while_the_turn_runs_steers_it(
    blocked: tuple[Composition, asyncio.Event], tmp_path: Path
) -> None:
    """The composer stays usable, and what it sends joins the turn instead of competing."""

    async def inner() -> None:
        composition, release = blocked
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press('g', 'o')
                await pilot.press('enter')
                await _until(pilot, lambda: app._turn_active, what='a running turn')

                await pilot.press('m', 'o', 'r', 'e')
                await pilot.press('enter')
                await _until(
                    pilot,
                    lambda: [row['kind'] for row in _inputs(runtime)] == ['turn', 'steer'],
                    what='the steer in the queue',
                )

                release.set()
                await _until(pilot, lambda: not app._turn_active, what='the end of the turn')
                assert app._service_steers == [], 'the service confirmed the steer it read'

    asyncio.run(inner())


def test_escape_twice_stops_the_turn_through_the_service(
    blocked: tuple[Composition, asyncio.Event], tmp_path: Path
) -> None:
    """The visible rule is unchanged: the first press asks, the second one stops."""

    async def inner() -> None:
        composition, _release = blocked
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press('g', 'o')
                await pilot.press('enter')
                await _until(pilot, lambda: app._turn_active, what='a running turn')

                await pilot.press('escape')
                assert app._turn_active is True
                assert 'again' in _status(app), 'the first Esc only asks'

                await pilot.press('escape')
                await _until(pilot, lambda: not app._turn_active, what='the turn stopping')
                await _until(pilot, lambda: 'Turn cancelled.' in _transcript(app), what='the cancellation')
                assert runtime.busy is False

    asyncio.run(inner())


def test_a_question_is_answered_through_the_client(composition: Composition, tmp_path: Path) -> None:
    """The panel is the same one; what changed is that the answer travels over HTTP."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                asked = asyncio.create_task(runtime.composition.question_hub.ask([_question()]))

                await _until(pilot, lambda: app.query_one(QuestionPanel).display, what='the question panel')
                await pilot.press('enter')

                answers = await asyncio.wait_for(asked, timeout=10)
                assert [answer.answer for answer in answers or []] == ['Recommended']
                assert runtime.pending_question is None
                assert await client.answer('q-does-not-exist', None) is False, 'a stale question is not retried'

    asyncio.run(inner())


def test_an_image_travels_in_order_with_the_marker_that_names_it(composition: Composition, tmp_path: Path) -> None:
    """The picture the composer attached is the picture the service stored."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.query_one(PromptInput).attach(prepare_image(_png()))
                await pilot.press('enter')

                await _until(pilot, lambda: bool(_inputs(runtime)), what='the accepted message')
                await _until(pilot, lambda: 'done' in _transcript(app), what='the answer')

                payload = _inputs(runtime)[0]['payload']
                assert payload['text'] == '[Image #1]'
                assert [image['media_type'] for image in payload['images']] == ['image/png']
                assert base64.b64decode(payload['images'][0]['data'])[:8] == b'\x89PNG\r\n\x1a\n'

    asyncio.run(inner())


def test_a_cut_stream_says_events_may_be_missing_and_re_reads_the_state(
    composition: Composition, tmp_path: Path
) -> None:
    """A break is not a silent freeze: the screen says so, resumes by cursor, refetches."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, client):
            transport.cut_after_replay = True
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: transport.streams >= 2, what='a reconnection')
                await _until(
                    pilot,
                    lambda: 'may be missing' in _transcript(app),
                    what='the notice about the gap',
                )
                assert app._service_state is not None, 'the state was re-read after the cut'

                # The reconnection is the point: a live stream again, same cursor.
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _until(pilot, lambda: 'done' in _transcript(app), what='the answer')

    asyncio.run(inner())


# --- attaching to a conversation that already exists -------------------------


async def _wait_until(predicate: Callable[[], bool], *, what: str, attempts: int = 2000) -> None:
    """Yields to the loop until the predicate holds. No fixed sleep, no guess."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f'the service never showed {what}')


async def _seed_turn(runtime: ServerRuntime, client: ServiceClient, text: str, *, image: bool = False) -> None:
    """One completed turn, so the service has a conversation to paint when we attach."""
    images = (OutgoingImage(media_type='image/png', data=base64.b64encode(_png()).decode('ascii')),) if image else ()
    await client.send_turn(TurnRequest(text=text, images=images))
    await asyncio.wait_for(runtime.wait(), timeout=20)


def test_attaching_to_a_session_with_history_paints_it(composition: Composition, tmp_path: Path) -> None:
    """The screen starts complete: the stream continues a conversation, it is not one.

    Before this, the terminal painted nothing until the next event arrived, so attaching
    to a session with history looked like an empty session. The durable read is the one
    request that carries both what was said and what is still waiting.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await _seed_turn(runtime, client, 'earlier question')
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: 'earlier question' in _transcript(app), what='the stored message')
                assert 'done' in _transcript(app), 'the stored answer is painted too'

    asyncio.run(inner())


def test_the_painted_history_is_not_painted_again_by_the_next_event(composition: Composition, tmp_path: Path) -> None:
    """The cursor starts after the state, and the history starts after the cursor.

    Both halves of the same risk: an event replayed from the buffer, or a rebuild that
    does not clear, would paint the conversation twice.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await _seed_turn(runtime, client, 'first question')
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: 'first question' in _transcript(app), what='the stored message')

                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _until(pilot, lambda: _transcript(app).count('done') == 2, what='the second answer')

                assert _transcript(app).count('first question') == 1
                assert _transcript(app).count('› hi') == 1

    asyncio.run(inner())


def test_a_pending_input_is_painted_once_and_leaves_when_confirmed(composition: Composition, tmp_path: Path) -> None:
    """Waiting is not history. It is painted once, as waiting, and taken down when read.

    The row is written straight into the queue instead of being produced by a turn held
    open across the mount. Two reasons, both about what the test is allowed to prove.
    Holding a turn open means the app mounts while another turn is mid-flight on the
    same loop, so the test would be asserting on the harness's timing as much as on the
    renderer — and it hung doing it. A queue row is the fact the renderer reads; seeding
    it directly makes the test measure that fact and nothing else.

    The confirmation is published on the service's own hub, which is exactly what a run
    reading the message does: the event carries the service's enqueue id, never the
    request id this terminal generated, so retiring by queue order is the contract.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await _seed_turn(runtime, client, 'earlier question')
            # The id comes from the same factory the composer uses: the queue requires a
            # canonical UUIDv4, and reusing `TurnRequest` means the seed cannot drift
            # from what a real send writes.
            composition.store.accept_input(
                runtime.session,
                TurnRequest(text='queued while busy').request_id,
                'queued while busy',
                [],
                'steer',
            )

            durable = await client.durable()
            assert [entry['text'] for entry in durable['pending']] == ['queued while busy']

            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: 'queued while busy' in _transcript(app), what='the waiting line')
                assert _transcript(app).count('queued while busy') == 1
                assert 'queued for the running turn' in _transcript(app)
                assert app._service_pending, 'the waiting line is owned by the app'

                runtime.hub.publish('steer_delivered', enqueue_id='enqueue-1')
                await _until(pilot, lambda: not app._service_pending, what='the confirmation')
                assert 'queued for the running turn' not in _transcript(app)

    asyncio.run(inner())


def test_history_images_keep_their_marker_and_are_described(composition: Composition, tmp_path: Path) -> None:
    """A stored picture is painted from what the service says it is, not from bytes."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await _seed_turn(runtime, client, '[Image #1]', image=True)
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: '[Image #1]' in _transcript(app), what='the image marker')
                assert '1 image' in _transcript(app)
                assert 'PNG' in _transcript(app)

    asyncio.run(inner())


# --- two terminals, one service ---------------------------------------------
#
# The scenario `specs/features/application/shared_sessions.feature` names "Two
# clients observe the same active session". It is proven here because the
# behaviour *is* the cross-client wiring: two `SigilApp`s, two `ServiceClient`s,
# one `ServerRuntime` and one hub. Nothing below the terminal can show that a
# screen which did not send anything still receives what another one sent, and a
# third layer would only repeat this one more expensively.

# Why the watcher is a second client and not a second app on the first one's
# state: a shared session is about the service, so the evidence has to travel the
# service's own road — `/api/state`, `/api/durable` and one `/events`
# subscription per terminal, each with its own cursor. The only shared object is
# the transport, which is what "the same service answered both" means here.


async def _both_until(
    pilots: tuple[Any, ...], predicate: Callable[[], bool], *, what: str, attempts: int = 600
) -> None:
    """Pauses both terminals until the predicate holds.

    Both are pumped, not just the one under the assertion: the screen being read
    has to be given the event loop as often as the one being typed into, or the
    test would be measuring how fast the other app yields.
    """
    for _ in range(attempts):
        if predicate():
            return
        for pilot in pilots:
            await pilot.pause()
    raise AssertionError(f'the terminals never showed {what}')


def test_two_terminals_on_one_session_see_one_turn_and_never_a_duplicate(
    composition: Composition, tmp_path: Path
) -> None:
    """Two clients, one session: one state, one transcript, one turn.

    The three things the scenario asks for, and each is read from a different
    place so none of them can be satisfied by the others: the state from the
    projection both clients were handed, the transcript from the two screens, and
    "only one execution" from the durable queue the service wrote.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, watching_client):
            sending_client = ServiceClient(SERVICE, transport=transport)
            try:
                # A conversation the service has already written a row for. The durable
                # read that paints an attaching screen is answered from the rows, and a
                # draft session has none until its first message — the seed the other
                # attaching tests in this file use.
                await _seed_turn(runtime, watching_client, 'before either terminal attached')
                turns_before = [row['kind'] for row in _inputs(runtime)]
                assert turns_before == ['turn']
                sender = _terminal(sending_client, _session(runtime))
                watcher = _terminal(watching_client, _session(runtime))
                # One statement, two apps: the exit order stays the watcher first,
                # because a context manager leaves in reverse order of entry.
                async with (
                    sender.run_test(size=(100, 40)) as sender_pilot,
                    watcher.run_test(size=(100, 40)) as watcher_pilot,
                ):
                    pilots = (sender_pilot, watcher_pilot)
                    await _both_until(
                        pilots,
                        lambda: sender._service_state is not None and watcher._service_state is not None,
                        what='the state on both screens',
                    )

                    # The same conversation, named to both by the service.
                    assert sender._service_state is not None
                    assert watcher._service_state is not None
                    assert sender._service_state['session'] == watcher._service_state['session']
                    assert sender._service_state['session']['id'] == runtime.session.id

                    # The baseline for the mechanism asserted below: both screens
                    # have already read the durable rows that paint the seeded
                    # conversation, so a later read is one they had to ask for.
                    await _both_until(
                        pilots,
                        lambda: (
                            'before either terminal attached' in _transcript(sender)
                            and 'before either terminal attached' in _transcript(watcher)
                        ),
                        what='the durable history on both screens',
                    )
                    durable_reads = transport.durable_reads

                    # One sends while the other only watches.
                    await sender_pilot.press('h', 'i')
                    await sender_pilot.press('enter')

                    await _both_until(
                        pilots,
                        lambda: 'done' in _transcript(sender) and 'done' in _transcript(watcher),
                        what='the answer on both screens',
                    )
                    await _both_until(
                        pilots,
                        lambda: not sender._turn_active and not watcher._turn_active,
                        what='the end of the turn on both screens',
                    )
                    assert 'done' in _transcript(watcher), 'the watcher followed the execution live'

                    # And it learns the message the *other* client typed, without the
                    # test doing anything: the service announces the acceptance with
                    # no text in it, the watching screen re-reads the rows, and the
                    # row paints once. This is the half of the scenario that used to
                    # need a reconnection.
                    await _both_until(
                        pilots,
                        lambda: '› hi' in _transcript(watcher),
                        what='the message the other terminal typed',
                    )

                    # The mechanism behind that line, not just the line: the watcher
                    # learned the message by asking the service for the durable rows
                    # again, on its own stream. A screen that painted the message
                    # locally would satisfy the assertion above and fail this one.
                    assert watcher._client is not None
                    assert watcher._service_following is True
                    assert transport.streams >= 2, 'each terminal follows on a connection of its own'
                    assert transport.durable_reads > durable_reads, 'the watcher re-read the durable rows'

                    # The history both screens started from, and the message only one
                    # of them typed: each painted once, on both.
                    for app in (sender, watcher):
                        assert 'before either terminal attached' in _transcript(app)
                        assert _transcript(app).count('› hi') == 1, 'the message was painted twice'
                        # One answer for the seed, one for this message: no turn ran
                        # twice, and neither answer was painted twice.
                        assert _transcript(app).count('done') == 2, 'an answer was painted twice'
                    assert _transcript(watcher) == _transcript(sender)

                    # A second turn is what a second executor would look like.
                    assert [row['kind'] for row in _inputs(runtime)] == [*turns_before, 'turn']
                    assert runtime.busy is False
            finally:
                await sending_client.aclose()

    asyncio.run(inner())


# --- a slash command in an attached terminal --------------------------------
#
# The catalogue declares who serves each command; the transport table in
# `src/ui/command_transport.py` says which of those this terminal has been wired
# to. These tests are the two halves of that answer, each read from where it would
# show: a client command from its effect on this window, a core command from the
# one notice it prints. Neither may open a panel or write a queue row, because
# "nothing else happened" is only meaningful against the rows it could touch.


def _client_command_effect(name: str) -> tuple[str, Callable[[SigilApp, str], bool]]:
    """The argument and the visible effect of one client command.

    Every client command has an entry. A new one without an entry raises here
    instead of passing untested: the transport table calls it "client", so
    something has to prove it runs in this process.
    """
    effects: dict[str, tuple[str, Callable[[SigilApp, str], bool]]] = {
        '/theme': ('synthwave', lambda app, _before: app.theme == 'synthwave'),
        '/sidebar': ('on', lambda app, _before: app.query_one(SidebarPanel).shown is True),
        '/help': (
            '',
            lambda app, before: 'This client' in _transcript(app) and len(_transcript(app)) > len(before),
        ),
        '/debug': ('on', lambda app, _before: app.debug_visible is True),
        '/tools': ('expanded', lambda app, _before: app._preferences.tools_expanded is True),
        '/reasoning-visibility': ('off', lambda app, _before: app._preferences.reasoning_visible is False),
        '/alerts': ('on', lambda app, _before: app._preferences.desktop_notifications is True),
        '/quit': ('', lambda app, _before: app._exit is True),
    }
    return effects[name]


@pytest.mark.parametrize('name', [command.name for command in owned_by(CommandOwner.CLIENT)])
def test_a_client_command_runs_here_and_writes_nothing(name: str, composition: Composition, tmp_path: Path) -> None:
    """A client command is this window's own work: it acts, and it travels nowhere.

    The three assertions answer three different questions. The effect says the
    command actually ran instead of being refused. The screen stack says it opened
    no panel it did not need. The durable rows say the service was not asked to
    write anything, which is what a command leaking into the queue would look like.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                before_transcript = _transcript(app)
                before = _inputs(runtime)
                args, effect = _client_command_effect(name)

                await app.handle_command(name, args)
                await pilot.pause()

                assert effect(app, before_transcript), f'{name} ran here but left no visible effect'
                assert len(app.screen_stack) == 1, 'a client command opens no panel it does not need'
                assert _inputs(runtime) == before, 'a client command writes nothing to the queue'

    asyncio.run(inner())


REFUSED = [row.name for row in TRANSPORT if row.state in {TransportState.NOT_WIRED, TransportState.BLOCKED}]
"""The rows with no way out: these are the ones that answer with one notice.

A `delegated` row is not in this list any more, and it should not be: it has a
sender, so refusing it would be the dispatcher ignoring its own table. The four
of them are exercised by the tests at the end of this file, which read the
service afterwards.
"""


@pytest.mark.parametrize('name', REFUSED)
def test_a_core_command_says_who_serves_it_and_leaves_the_screen_alone(
    name: str, composition: Composition, tmp_path: Path
) -> None:
    """A core command this terminal cannot reach is one sentence, not a failure.

    "Exactly one notice" is counted on the blocks the command added, not on the
    transcript as a whole: a picker, a second notice or a repaint all add a block,
    and the command is supposed to add exactly one. `screen_stack` catches the
    panel a handler would have pushed; the durable rows catch the write a handler
    would have made.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                blocks_before = len(chat.children)
                before = _inputs(runtime)

                await app.handle_command(name)
                await pilot.pause()

                added = list(chat.children)[blocks_before:]
                assert len(added) == 1, f'{name} answers in exactly one notice'
                assert name in _block_text(added[0]), 'the notice names the command it could not run'
                assert len(app.screen_stack) == 1, 'a command that cannot act opens no panel'
                assert _inputs(runtime) == before, 'a command that cannot act writes nothing'

    asyncio.run(inner())


def test_a_client_command_is_not_refused_whole_sale_in_an_attached_terminal(
    composition: Composition, tmp_path: Path
) -> None:
    """The regression for the defect: the dispatcher used to be replaced entirely.

    `SigilApp` swapped `handle_command` for a refusal, so every command answered the
    same sentence — including the eight that only repaint this window. `/theme` is
    the one with the most visible effect, so a dispatcher that refuses everything
    fails here even when the table itself is right.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()

                await app.handle_command('/theme', 'synthwave')
                await pilot.pause()

                assert app.theme == 'synthwave', 'the theme command repainted this terminal'
                assert 'not wired' not in _transcript(app), 'and nothing refused it'

    asyncio.run(inner())


SIDE_DOORS = (('ctrl+a', '/agent'),)
"""The keys that used to open a picker without passing the dispatcher.

`ctrl+l` and `ctrl+m` left this list when their commands were delegated: `/effort`
and `/model` open a picker that is the client's, fed by the service, so a panel is
the correct answer and not a bypass. What this test still proves is the part that
was the bug — a key and its typed command reaching the same decision.

The prompt handled them itself and called `open_models`, `open_effort` and
`open_agent` directly, so an attached terminal bypassed whichever decision the
typed command made. Now they go through the same choke point, and this is the
proof: the key and the command produce the same sentence, character for
character.

The model picker is `ctrl+m`, not `alt+m`: `tests/test_shortcuts_and_efforts.py`
locks that, because a global `alt+m` binding is what the pickers moved away from.

`ctrl+l` is deliberately absent: `/effort` is delegated now, so its door opens the
picker fed by the service instead of answering a refusal. Its own test is below.
"""


@pytest.mark.parametrize(('key', 'name'), SIDE_DOORS)
def test_a_side_door_answers_exactly_like_the_typed_command(
    key: str, name: str, composition: Composition, tmp_path: Path
) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                before = _inputs(runtime)

                blocks_before = len(chat.children)
                await pilot.press(key)
                await _until(pilot, lambda: len(chat.children) > blocks_before, what=f'the {key} notice')
                from_key = '\n'.join(_block_text(widget) for widget in list(chat.children)[blocks_before:])

                blocks_before = len(chat.children)
                await app.handle_command(name)
                await pilot.pause()
                from_command = '\n'.join(_block_text(widget) for widget in list(chat.children)[blocks_before:])

                assert name in from_key, f'{key} answers for {name}'
                assert from_key == from_command, 'the key and the typed command answer the same'
                assert len(app.screen_stack) == 1, 'a side door opens no panel in an attached terminal'
                assert _inputs(runtime) == before, 'a side door writes nothing'

    asyncio.run(inner())


# --- a service that restarted ------------------------------------------------
#
# A restart is not a reconnection. The process is gone, and the one that replaced
# it listens on another port with another token, so the address this client was
# built with is dead for good. Two things have to change together: the endpoint,
# and the event cursor — the second because sequence numbers are per run, and an
# old cursor would make the new service replay past everything the terminal never
# received.

RESTARTED = Endpoint(host='gone.test', port=9, pid=0, token='token-of-the-run-that-restarted', started_at=0.0)
"""The endpoint the terminal was built with: a service that has since restarted.

The token differs from `SERVICE` on purpose. A restarted run publishes a new
credential, so a client that kept the old header would be refused by the service
that is alive — which is what makes the token half of the rebuild observable
instead of assumed.
"""


class _RestartedService(httpx.AsyncBaseTransport):
    """One transport, two addresses: the old one refuses, the live one answers.

    The transport outlives the rebuild, so the client cannot quietly be handed a
    second one: it decides by address, which is the only thing that changed.
    Everything behind the live address is the real application — same routes, same
    bearer check, same hub — reusing the seam `_ServiceTransport` already explains.
    """

    def __init__(self, live: Endpoint, inner: httpx.AsyncBaseTransport) -> None:
        self._live = live
        self._inner = inner
        self.refused = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self._is_live(request):
            self.refused += 1
            raise httpx.ConnectError('Connection refused', request=request)
        return await self._inner.handle_async_request(request)

    def _is_live(self, request: httpx.Request) -> bool:
        """Whether the request is addressed to the service that is running.

        The port is read with its default, because httpx normalises ``:80`` out of a
        URL: comparing ``request.url.port`` directly would answer ``None`` for the
        live address and refuse it forever, which looks exactly like a client that
        never recovered.
        """
        scheme_default = 443 if request.url.scheme == 'https' else 80
        port = request.url.port or scheme_default
        return (request.url.host, port) == (self._live.host, self._live.port)


def test_a_terminal_recovers_when_the_service_comes_back_on_another_endpoint(
    composition: Composition, tmp_path: Path
) -> None:
    """The screen follows the service, not the address it was told once.

    The old address is dead from the first request, so nothing can succeed until the
    client re-aims itself: a recovered state is proof that the discovery, the rebuild
    and the new token all worked. The cursor is asserted separately, because a
    terminal that reconnected while keeping its old sequence number would look
    healthy and skip the new run's events — the failure this test exists to catch.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _attached):
            # The run that is alive has already published something, so its state
            # names a sequence number the dead run's cursor could never reach.
            runtime.hub.publish('notice', text='after the restart', tone='warn')
            scripted = _RestartedService(SERVICE, transport)
            client = ServiceClient(RESTARTED, transport=scripted, launch=lambda **_: Launch(endpoint=SERVICE))
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the recovered service')

                    assert scripted.refused >= 1, 'the dead address was tried before the rebuild'
                    assert app._service_cursor == runtime.hub.last_seq > 0, 'the cursor restarts with the run'
                    assert app._service_following is True, 'and the new run is being followed'

                    # Recovered means usable, not merely reconnected: a message
                    # travels on the new endpoint and its answer paints.
                    await pilot.press('h', 'i')
                    await pilot.press('enter')
                    await _until(pilot, lambda: 'done' in _transcript(app), what='the answer')
                    assert [row['kind'] for row in _inputs(runtime)] == ['turn']
            finally:
                await client.aclose()

    asyncio.run(inner())


# --- the cursor a restart must not inherit -----------------------------------
#
# A restart changes the sequence space, and the way that hurts is narrow enough to
# state precisely: the new run numbers from one, so an old cursor that is *ahead* of
# the new run makes `events(after=N)` replay nothing and the terminal silently skips
# everything published before it caught up. Proving that needs both halves of the
# shape — an old run that got further than the new one, and a new run whose events
# already exist when the stream connects. A restart test where the new run is ahead
# cannot fail: the stale cursor would replay a subset and look healthy.

FIRST_RUN = Endpoint(host='first.test', port=80, pid=0, token=TOKEN, started_at=0.0)
"""The run the terminal attached to: five events published, all of them applied."""

SECOND_RUN = Endpoint(host='second.test', port=80, pid=0, token=TOKEN, started_at=0.0)
"""The run that replaced it: its state is taken at zero, its stream holds two events.

The token is the same on purpose. The real application answers both addresses here,
so it is the same credential; that a restart also publishes a new token is proven a
level down, in `tests/test_ui_service_client.py`, where the rebuild is the subject.
"""


class _CutableStream(httpx.AsyncByteStream):
    """Server-sent events, then an open connection the test can end."""

    def __init__(self, chunks: list[bytes], done: asyncio.Event) -> None:
        self._chunks = chunks
        self._done = done

    def end(self) -> None:
        """Ends the connection the way a service dying does: without a goodbye."""
        self._done.set()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        await self._done.wait()


class _TwoRuns(httpx.AsyncBaseTransport):
    """One application behind two addresses, with a different stream for each.

    Every non-stream route is the real app, so the state and the durable read are the
    service's own. The stream is the part that has to differ, because a sequence space
    is per run and no single hub can be in two runs at once: the first address replays
    five events, the second replays two, and both report `seq: 0` from the real app
    whose hub never publishes here. That is a run whose state was taken before those
    events existed — exactly the window a stale cursor falls into.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        old: Endpoint,
        new: Endpoint,
        old_events: int,
        new_events: int,
    ) -> None:
        self._inner = inner
        self._old = old
        self._counts = {old.host: old_events, new.host: new_events}
        self._labels = {old.host: 'the old run, event', new.host: 'the new run, event'}
        self.dead = False
        self.cursors: list[tuple[str, int]] = []
        self.streams: dict[str, _CutableStream] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host or ''
        if host == self._old.host and self.dead:
            raise httpx.ConnectError('Connection refused', request=request)
        if request.url.path != '/events':
            return await self._inner.handle_async_request(request)
        after = _after(request)
        self.cursors.append((host, after))
        stream = _CutableStream(self._events(host, after), asyncio.Event())
        self.streams[host] = stream
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)

    def _events(self, host: str, after: int) -> list[bytes]:
        """The events this run would replay after ``after``, as their own SSE bytes."""
        label = self._labels[host]
        return [
            Event(seq=seq, type='notice', data={'text': f'{label} {seq}', 'tone': 'warn'}).to_sse().encode()
            for seq in range(after + 1, self._counts[host] + 1)
        ]


def test_a_restart_does_not_inherit_the_old_cursor_so_no_event_of_the_new_run_is_skipped(
    composition: Composition, tmp_path: Path
) -> None:
    """The new run's first event is applied, which is only true if the cursor reset.

    The old run reached five; the new one has two, and its state was taken at zero.
    A terminal that kept the cursor would open `events(after=5)` against a run that
    has never published a fifth event: nothing is replayed, nothing is announced, and
    the two events of the live run are simply never seen. So the assertions are the
    cursor the new stream was opened with, the cursor the screen ended on, and both
    lines on screen — the first of them is the one a stale cursor loses.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _attached):
            scripted = _TwoRuns(transport, old=FIRST_RUN, new=SECOND_RUN, old_events=5, new_events=2)
            client = ServiceClient(FIRST_RUN, transport=scripted, launch=lambda **_: Launch(endpoint=SECOND_RUN))
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    # The old run, followed for as long as it lasts: five events applied.
                    await _until(pilot, lambda: app._service_cursor == 5, what='the old run, applied')
                    assert scripted.cursors[0] == (FIRST_RUN.host, 0)

                    # The old run dies. The terminal notices the cut, cannot reach the
                    # old address again, and recovers onto the new one.
                    scripted.dead = True
                    scripted.streams[FIRST_RUN.host].end()

                    await _until(
                        pilot,
                        lambda: 'the new run, event 1' in _transcript(app),
                        what='the first event of the new run',
                    )
                    assert 'the new run, event 2' in _transcript(app), 'the event after it too'
                    assert app._service_cursor == 2, 'the screen is on the new run, not ahead of it'
                    assert scripted.cursors[-1] == (SECOND_RUN.host, 0), 'the new stream starts at the new state'
                    assert [cursor for host, cursor in scripted.cursors if host == SECOND_RUN.host] == [0]
            finally:
                await client.aclose()

    asyncio.run(inner())


def test_attaching_to_a_session_with_no_history_shows_nothing_and_keeps_listening(
    composition: Composition, tmp_path: Path
) -> None:
    """A conversation that has not started yet is not a failure.

    `Composition` opens a *draft* session — an id with nothing saved under it — so the
    durable read answers 404. Before that answer was typed, it reached the screen as
    "the terminal and the service are different versions": an alarming sentence about a
    service that was answering correctly, printed over an empty conversation. The 404
    now means "no history yet", the screen stays quiet, and the first turn fills it.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.pause()

                assert _transcript(app) == '', 'a session with no history paints no history'
                assert '✖' not in _transcript(app), 'a draft session is not an error'
                assert app._service_state is not None, 'the screen is attached and following'

                # The first message gives the session a row, and the screen fills in.
                await client.send_turn(TurnRequest(text='the first message'))
                await _until(
                    pilot,
                    lambda: 'the first message' in _transcript(app) and 'done' in _transcript(app),
                    what='the first turn painted from the rows',
                )

    asyncio.run(inner())


# --- the four commands that travel -------------------------------------------
#
# Each of these reads the *service's* state afterwards, never the `200` the client
# got: the claim is that the session changed, and only the owner of the session can
# answer that.


def test_the_service_context_report_never_invents_a_number() -> None:
    """A cold session is a real zero, and an absent datum says so instead of `?`.

    This is the half the old report got wrong: it painted this process's meter,
    which for an attached terminal has measured nothing, so every number was a
    confident zero about a conversation it had never seen.
    """
    text = render_service_context(
        {
            'window': 100_000,
            'used': 0,
            'percent': 0.0,
            'measured': None,
            'projected': None,
            'budget': {
                'tools': 10,
                'instructions': 20,
                'output_reserve': 30,
                'safety_reserve': 40,
                'available_for_history': 50,
            },
            'cost': {'total': None, 'approximate': False, 'requests': 0},
        }
    )

    assert '0 (0%)' in text
    assert '?' not in text
    assert 'not reported by the service' in text
    assert 'not priced yet' in text


def test_the_service_context_report_names_the_workspace_it_was_given() -> None:
    text = render_service_context(
        {'window': None, 'used': None, 'percent': None, 'measured': None, 'projected': None, 'budget': {}, 'cost': {}},
        {'label': 'sigil', 'root': '/repo', 'branch': 'main', 'worktree': {'is_main': True}},
    )

    assert 'Workspace' in text
    assert '/repo' in text
    assert 'main' in text


def test_context_paints_the_services_measurement(composition: Composition, tmp_path: Path) -> None:
    """The number on screen is the service's, read from the service itself."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                expected = await client.context()

                chat = app.query_one(ChatLog)
                before = len(chat.children)
                await app.handle_command('/context')
                await _until(pilot, lambda: len(chat.children) > before, what='the context report')
                block = '\n'.join(_block_text(widget) for widget in list(chat.children)[before:])

                assert f'{expected["used"]:,}' in block
                assert expected['window'] and f'{expected["window"]:,}' in block
                assert '?' not in block, 'no placeholder where the service sent a number'

    asyncio.run(inner())


def test_external_files_changes_the_service_and_never_the_local_session(
    composition: Composition, tmp_path: Path
) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/external-files', 'allow')
                await pilot.pause()

                # The service's session is the one that moved.
                assert runtime.session.allow_external_files is True
                # And this terminal's copy did not: one setting, one writer.
                assert app._session.allow_external_files is False

    asyncio.run(inner())


def test_a_refused_external_files_change_says_why_and_changes_nothing(composition: Composition, tmp_path: Path) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                async with _busy(runtime):
                    await app.handle_command('/external-files', 'allow')
                    await pilot.pause()

                assert runtime.session.allow_external_files is False, 'the refusal changed nothing'
                assert 'A turn is running' in _transcript(app), "the service's own reason"

    asyncio.run(inner())


def test_effort_changes_the_services_model(composition: Composition, tmp_path: Path) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/effort', 'high')
                await pilot.pause()

                assert runtime.composition.model_service.snapshot().thinking == 'high'
                assert 'Reasoning effort: high' in _transcript(app)

    asyncio.run(inner())


def test_a_refused_effort_change_says_why_and_changes_nothing(composition: Composition, tmp_path: Path) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                before = runtime.composition.model_service.snapshot().thinking

                async with _busy(runtime):
                    await app.handle_command('/effort', 'high')
                    await pilot.pause()

                assert runtime.composition.model_service.snapshot().thinking == before
                assert 'A turn is in progress' in _transcript(app)

    asyncio.run(inner())


def test_mcp_lists_the_services_servers_and_toggles_them_there(tmp_path: Path, monkeypatch: Any) -> None:
    """The modal is the client's; the servers and the toggle are the service's."""
    composition = _composition(tmp_path, monkeypatch, mcp_servers={'probe': {'command': 'npx', 'prefix': ''}})
    composition.mcp.install(build_manager(composition.mcp_config.servers, None))

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                app.run_worker(app.handle_command('/mcp'))
                await _until(pilot, lambda: isinstance(app.screen, MCPScreen), what='the MCP modal')

                options = app.screen.query_one('OptionList')
                assert [option.id for option in options.options] == ['probe']

                app.screen.query_one('OptionList').highlighted = 0
                await pilot.press('enter')
                await _until(pilot, lambda: runtime.session.mcp_overrides == {'probe': False}, what='the toggle')

    asyncio.run(inner())


# --- /agent, /new and /load travel -------------------------------------------
#
# Each of these reads the SERVICE afterwards, never the local projection: the
# claim is that the service's session changed, and only its owner can answer that.
# The local `Session` an attached terminal holds is a reading, so it is asserted
# *not* to move — that is what "the terminal stops writing session identity
# locally" means in practice.


def test_the_agent_command_switches_the_services_session(composition: Composition, tmp_path: Path) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/agent', 'reviewer')
                await pilot.pause()

                assert runtime.composition.agent_registry.active_name == 'reviewer'
                assert (await client.state())['session']['agent'] == 'reviewer'
                assert app._session.agent == '', 'the terminal must not stamp the session itself'
                assert 'Agent: Reviewer' in _transcript(app)

    asyncio.run(inner())


def test_a_second_terminal_sees_the_agents_change_after_refreshing(composition: Composition, tmp_path: Path) -> None:
    """Two clients, one session: the second one catches up from the service."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            first = _terminal(client, _session(runtime))
            async with first.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: first._service_state is not None, what='the state')
                await first.handle_command('/agent', 'reviewer')
                await pilot.pause()

            second = _terminal(client, _session(runtime))
            async with second.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: second._service_state is not None, what='the state')
                await pilot.pause()

                assert 'Reviewer' in str(second.query_one(StatusBar).render())

    asyncio.run(inner())


def test_new_creates_the_session_in_the_service(composition: Composition, tmp_path: Path) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                before = runtime.session.id

                await app.handle_command('/new')
                await pilot.pause()

                assert runtime.session.id != before
                # The reading this terminal holds is not the new session: the row,
                # the id and the identity are the service's.
                assert app._session.id == before
                assert f'New session {runtime.session.short_id}' in _transcript(app)
                # The cursor restarts from the state just read, not from the old run.
                assert app._service_cursor == app._state_cursor()

    asyncio.run(inner())


def test_new_with_a_worktree_says_it_is_not_supported(composition: Composition, tmp_path: Path) -> None:
    """The honest answer: no session, no worktree cut, no pretending."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                before = runtime.session.id

                await app.handle_command('/new', '--worktree fresh')
                await pilot.pause()

                assert runtime.session.id == before, 'nothing was created'
                assert 'not supported from an attached terminal' in _transcript(app)
                assert 'worktree first' in _transcript(app)

    asyncio.run(inner())


def test_load_opens_the_chosen_session_through_the_service(composition: Composition, tmp_path: Path) -> None:
    """Listed from `/api/sessions`, opened by id, repainted from the state.

    The picker widget is the client's and has its own test, so the screen is
    stubbed here: what this proves is that the command asks the service for the
    list and that opening the choice moves the *service's* session.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            first = runtime.session.id
            await client.send_turn(TurnRequest('first'))
            await runtime.wait()
            created = await client.new_session()
            assert runtime.session.id == created['id']

            app = _terminal(client, _session(runtime))

            async def choose(_screen: object) -> str:
                return first

            app.push_screen_wait = choose  # type: ignore[method-assign]
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/load')
                await pilot.pause()

                assert runtime.session.id == first
                assert f'Loaded session {first[:8]}' in _transcript(app)

    asyncio.run(inner())


# --- /model, /rollback and /fork travel ---------------------------------------


class _Chooser:
    """Answers the client's screens from a list, so no widget has to be driven.

    The screens themselves have their own tests; what these prove is that the list
    the screen is fed came from the service and that the effect landed there.
    """

    def __init__(self, returns: list[Any]) -> None:
        self._returns = list(returns)

    async def __call__(self, _screen: object) -> Any:
        return self._returns.pop(0)


def test_model_switch_changes_the_services_model(composition: Composition, tmp_path: Path) -> None:
    from src.engine.providers.domain.model_capability import ModelCapability
    from tests.test_ui_service_client import _Catalog, _with_catalog

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _with_catalog(
                runtime,
                _Catalog((ModelCapability(name='second/model', context_window=200_000, efforts=('low', 'high')),)),
            )
            alias = runtime.composition.model_service.accounts.load()[0].alias
            app = _terminal(client, _session(runtime))
            app.push_screen_wait = _Chooser(
                [
                    SimpleNamespace(alias=alias, model=SimpleNamespace(name='second/model', efforts=('low', 'high'))),
                    SimpleNamespace(effort='high'),
                ]
            )
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/model')
                await pilot.pause()

                assert runtime.composition.model_service.snapshot().model == 'second/model'
                assert 'Model: ' in _transcript(app)

    asyncio.run(inner())


def test_rollback_truncates_the_services_session_and_restores_the_prompt(
    composition: Composition, tmp_path: Path
) -> None:
    from src.ui.widgets.rollback_screen import ROLLBACK_ONLY

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await client.send_turn(TurnRequest('explain the queue'))
            await runtime.wait()
            points = (await client.rollback_points())['points']
            assert points

            app = _terminal(client, _session(runtime))
            app.push_screen_wait = _Chooser([SimpleNamespace(turn_id=points[0]['turn_id'], action=ROLLBACK_ONLY)])
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/rollback')
                await pilot.pause()

                # The rows are the service's, and they are shorter now.
                assert (await client.rollback_points())['points'] == []
                assert 'Rolled back' in _transcript(app)
                # The prompt came back in the reply, not from a local row.
                assert 'explain the queue' in app.query_one(PromptInput).value

    asyncio.run(inner())


def test_fork_creates_a_session_in_the_service_and_keeps_this_one(composition: Composition, tmp_path: Path) -> None:
    from src.ui.widgets.rollback_screen import FORK

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await client.send_turn(TurnRequest('first'))
            await runtime.wait()
            open_session = runtime.session.id
            before = len((await client.sessions())['sessions'])
            points = (await client.rollback_points())['points']

            app = _terminal(client, _session(runtime))
            app.push_screen_wait = _Chooser([SimpleNamespace(turn_id=points[0]['turn_id'], action=FORK)])
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/rollback')
                await pilot.pause()

                assert len((await client.sessions())['sessions']) == before + 1
                assert runtime.session.id == open_session, 'a fork destroys nothing'
                assert 'Forked to' in _transcript(app)

    asyncio.run(inner())


# --- a delegated command is refused before it opens anything ------------------
#
# The service would refuse the change anyway. The point is that the terminal reads
# `busy` from `/api/state` and answers with one line, instead of opening a picker,
# fetching a catalogue and only then being told the session is in the way.

DELEGATED_STATE_CHANGERS = ('/agent', '/model', '/effort')


@pytest.mark.parametrize('name', DELEGATED_STATE_CHANGERS)
def test_a_delegated_command_opens_no_panel_while_a_turn_runs(
    name: str, composition: Composition, tmp_path: Path
) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                async with _busy(runtime):
                    await app.refresh_service_state()
                    await pilot.pause()

                    chat = app.query_one(ChatLog)
                    before = len(chat.children)
                    inputs_before = _inputs(runtime)
                    agent_before = runtime.session.agent

                    await app.handle_command(name)
                    await pilot.pause()

                    added = list(chat.children)[before:]
                    assert len(added) == 1, f'{name} answers in exactly one notice'
                    text = _block_text(added[0])
                    assert name in text, 'the notice names the command'
                    assert 'A turn is running' in text, 'and uses the sentence the service uses'
                    assert len(app.screen_stack) == 1, f'{name} opened a panel it cannot complete'
                    assert _inputs(runtime) == inputs_before, 'nothing was written to the service'
                    assert runtime.session.agent == agent_before

    asyncio.run(inner())


@pytest.mark.parametrize('key', ('ctrl+a', 'ctrl+m', 'ctrl+l'))
def test_a_side_door_opens_no_panel_while_a_turn_runs(key: str, composition: Composition, tmp_path: Path) -> None:
    """The three keys reach the same gate as the typed commands."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                async with _busy(runtime):
                    await app.refresh_service_state()
                    await pilot.pause()

                    chat = app.query_one(ChatLog)
                    before = len(chat.children)

                    await pilot.press(key)
                    await _until(pilot, lambda: len(chat.children) > before, what=f'the {key} refusal')

                    assert len(app.screen_stack) == 1, f'{key} opened a panel it cannot complete'
                    assert 'A turn is running' in '\n'.join(
                        _block_text(widget) for widget in list(chat.children)[before:]
                    )

    asyncio.run(inner())
