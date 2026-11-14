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
import os
import re
import subprocess
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage
from textual.widgets import OptionList

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.attachments.images import prepare_image
from src.engine.bootstrap import Composition
from src.engine.commands import CommandOwner, owned_by
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_option import QuestionOption
from src.engine.mcp.config.loader import load_config
from src.engine.mcp.deferred import build_manager
from src.engine.mcp.manager.manager import MCPManager
from src.engine.preferences import (
    DEFAULT_TRANSCRIPT_WINDOW,
    MAX_TRANSCRIPT_WINDOW,
    Preferences,
    PreferencesStore,
)
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.sessions.session_store import Session
from src.engine.settings import load_settings
from src.server.app import create_app
from src.server.config import ServerConfig
from src.server.endpoint import Endpoint, EndpointPublisher
from src.server.events import Event, EventHub
from src.server.launch import Launch
from src.server.runtime import ServerRuntime
from src.ui import command_transport
from src.ui.app import SigilApp
from src.ui.command_transport import TRANSPORT, Transport, TransportState
from src.ui.commands_mixin import BUSY_UNKNOWN, render_service_context
from src.ui.rollback_mixin import _turn_for_seq
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
from src.ui.token_format import compact_tokens
from src.ui.tree_mixin import _nodes_from_tree
from src.ui.turn_mixin import ABORT_HINT, failure_notice, pending_request
from src.ui.widgets.agent_tree_screen import AgentTreeScreen, TreeAction
from src.ui.widgets.agent_view import AgentView
from src.ui.widgets.chat_log import ChatLog, ToolCallBlock, format_tool_args
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.mcp_screen import MCPScreen
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel
from src.ui.widgets.rollback_screen import RollbackScreen
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar
from tests.test_agents import write_profile
from tests.test_provider_service import _add, _service
from tests.test_server_parity import _busy
from tests.ui_helpers import render_text

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
    root: Path | None = None,
    output: str = 'done',
) -> Composition:
    """A real composition with one configured account and a scripted model.

    The account is injected by the provider test's own helper, so nothing probes a
    network, and the model is `TestModel`: the behaviour under test is the wiring
    between terminal, route, runtime and engine, not a provider.

    The MCP config is pinned to this test's directory, `mcp_servers` or empty.
    `config_paths()` otherwise falls back to `~/.sigil/config.json`, and the
    serving runtime now installs the layer on the first turn: unpinned, a test
    would start whatever servers the machine happens to have configured.

    ``root`` is the workspace the engine runs in, when it is not the test's own
    directory: cutting a worktree needs a git repository, and a test that only
    needs one builds the composition inside it.
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
        root or tmp_path,
        settings=load_settings(),
        model=TestModel(custom_output_text=output, call_tools=list(call_tools)),
        provider_paths=ProviderPaths(tmp_path / 'providers'),
    )
    composition.meter.set_window(100_000)
    return composition


class _HubStream(httpx.AsyncByteStream):
    """One `/events` body, written by the hub the application publishes into.

    ``drop`` removes whole event types from this connection, which is how a test
    stands in for the events a screen never received: a terminal cannot tell a
    stream that never carried a call from one whose buffer forgot it, and both are
    the same hole in what that screen knows about the turn.
    """

    def __init__(
        self,
        hub: EventHub,
        after: int,
        *,
        stop_after_replay: bool = False,
        drop: frozenset[str] = frozenset(),
    ) -> None:
        self._hub = hub
        self._after = after
        self._stop_after_replay = stop_after_replay
        self._drop = drop

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async with self._hub.subscribe() as subscription:
            for event in self._hub.replay(self._after):
                if event.type in self._drop:
                    continue
                yield event.to_sse().encode()
            if self._stop_after_replay:
                return
            while True:
                event = await subscription.next()
                if event.type in self._drop:
                    continue
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

    def __init__(self, app: Any, hub: EventHub, *, token: str = TOKEN, drop: frozenset[str] = frozenset()) -> None:
        self._inner = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._hub = hub
        self.app = app
        """The application, so a test can open another connection to this same service."""
        self.hub = hub
        """The hub the `/events` bodies are written from, shared by every connection."""
        self._drop = drop
        self._token = token
        self.streams = 0
        self.cursors: list[int] = []
        self.cut_after_replay = False
        self.answer_failure: Exception | None = None
        """The failure `/api/answer` should raise, or ``None`` for the real route.

        The route itself is the service's; what a test cannot get from it is a POST
        that never arrives, which is the case the screen has to survive without
        believing the question was answered.
        """
        self.durable_reads = 0
        """How many times a screen asked for the durable rows.

        The transcript a watching terminal paints after another one typed is read
        from `/api/session/durable`, never invented locally, so this counter is the
        mechanism a test watches: if the watcher painted the message without asking
        the service, the read would not happen and the number would not move.
        """
        self.pending_reads = 0
        """How many times a screen asked for the waiting rows and nothing else.

        Mid-turn a queue event is about the wait: the transcript is already being
        streamed into the screen. Reading the whole durable session there would
        decode a conversation nobody asked for, so this counter is how a test proves
        the queue overlay came from its own route.
        """
        self.pending_gate: asyncio.Event | None = None
        """When set, a `/api/session/pending` read waits on it before answering.

        A burst of queue events arrives while a read is in flight; a gate is how a
        test holds that read open instead of guessing at timing.
        """
        self.pending_in_flight = 0
        """How many pending reads have started and not answered yet."""
        self.state_reads = 0
        """How many times a screen re-read the state, in the order it asked."""
        self.transcript_reads = 0
        """How many transcript pages this connection asked for.

        A rebuild is a page read, so this is how a test proves the screen painted the
        rows again instead of only what its stream happened to carry.
        """
        self.defaults_writes: list[dict[str, Any]] = []
        """The bodies of the defaults writes, in the order they were sent.

        A write is the one thing an attached `/default-models` may do to the
        service's files, and the only way to do it is this POST: a terminal that
        edited the file itself — the failure this cut removes — would leave this
        list empty and the file changed beside it.
        """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/session/durable':
            self.durable_reads += 1
        if request.url.path == '/api/session/pending':
            self.pending_reads += 1
            self.pending_in_flight += 1
            try:
                if self.pending_gate is not None:
                    await self.pending_gate.wait()
            finally:
                self.pending_in_flight -= 1
        if request.url.path == '/api/state':
            self.state_reads += 1
        if request.url.path == '/api/session/transcript':
            self.transcript_reads += 1
        if request.url.path == '/api/model-defaults' and request.method == 'POST':
            self.defaults_writes.append(json.loads(request.content or b'{}'))
        if request.url.path == '/api/answer' and self.answer_failure is not None:
            raise self.answer_failure
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
            stream=_HubStream(self._hub, _after(request), stop_after_replay=cut, drop=self._drop),
        )

    def fork(self, *, drop: frozenset[str] = frozenset()) -> _ServiceTransport:
        """Another connection to the same service, missing the events in ``drop``.

        A second terminal differs from the first in its ``ServiceClient``, not in
        where the service lives: the same application answers it and the same hub
        writes its stream. What it does not get is the event types named here, which
        is the shape of a stream that lost them.
        """
        return _ServiceTransport(self.app, self.hub, token=self._token, drop=drop)


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


def _terminal(client: ServiceClient, session: Session, *, preferences: Preferences | None = None) -> SigilApp:
    """The production shape of the app: a service client and nothing to execute.

    The reconnect delay is zeroed so a cut stream is re-followed in the same pause
    the test is already waiting on. ``preferences`` pins what the screen reads from
    the user's own ``ui.json`` — the transcript window above all — so a test measures
    its own choices instead of the machine's.
    """
    app = SigilApp(client=client, session=session, settings=load_settings(), preferences=preferences)
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


def _tool_rows(app: SigilApp) -> list[tuple[str, str]]:
    """Every call on screen, as the name and the arguments its header draws.

    A tool row is not a `Static` holding a `Text`: it builds a `Group` from its own
    state, so reading the transcript would measure a repr instead of the row. These
    two are the attributes the header is built from, which is what the reader sees.
    """
    return [(block.name, format_tool_args(block._args)) for block in app.query_one(ChatLog).query(ToolCallBlock)]


def _submit(app: SigilApp, text: str) -> None:
    """Submits a command through the composer, which is where a worker is started.

    `push_screen_wait` needs a worker: a screen command awaited straight from the test's
    coroutine raises `NoActiveWorker` instead of opening the panel a user gets. The
    composer is the way a user takes, so it is also what proves the wiring.
    """
    field = app.query_one(PromptInput)
    field.value = text
    field.post_message(PromptInput.Submitted(text))


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


def _git(root: Path, *args: str) -> None:
    """One git command with an identity of its own, so no test depends on the user's."""
    subprocess.run(
        ['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test', *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one commit, so a worktree has a HEAD to branch from."""
    root = tmp_path / 'project'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / 'README.md').write_text('hello\n', encoding='utf-8')
    _git(root, 'add', '.')
    _git(root, 'commit', '-q', '-m', 'init')
    return root


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


def test_a_failed_answer_is_not_counted_as_answered(composition: Composition, tmp_path: Path) -> None:
    """A POST that never arrived leaves the question pending, and it is offered again.

    The send can fail for reasons the screen cannot fix — the service restarted, the
    network moved — and the one thing it must not do is decide the question is over:
    the service is still waiting, so the next state read has to offer it again.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                asked = asyncio.create_task(runtime.composition.question_hub.ask([_question()]))

                await _until(pilot, lambda: app.query_one(QuestionPanel).display, what='the question panel')
                identifier = str(runtime.pending_question.id if runtime.pending_question else '')
                assert identifier
                transport.answer_failure = httpx.ConnectError('the answer never left this process')

                await pilot.press('enter')
                await _until(pilot, lambda: 'No Sigil service answered' in _transcript(app), what='the failure notice')

                assert identifier not in app._service_answered, 'a failed send must not mark the question answered'
                assert runtime.pending_question is not None, 'the service is still waiting for the answer'

                # The reconnection, or any state read, is what offers it again.
                await app.refresh_service_state()
                await _until(pilot, lambda: app.query_one(QuestionPanel).display, what='the question offered again')

                transport.answer_failure = None
                await pilot.press('enter')
                answers = await asyncio.wait_for(asked, timeout=10)
                assert [answer.answer for answer in answers or []] == ['Recommended']
                assert runtime.pending_question is None

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
            # Nothing may claim the row while the renderer paints it. A steer the
            # runtime finds with no run to hand it to becomes `stopped` on purpose,
            # and the consumer looks every `QUEUE_POLL_INTERVAL` — so leaving that
            # loop running makes the test race a timer, and it loses under load with
            # the waiting line gone before the first read can paint it. Stopping the
            # consumer turns "still queued" into a state the test sets.
            consumer = runtime._consumer
            assert consumer is not None, 'the seed above starts the consumer'
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            assert consumer.cancelled(), 'a consumer still running would stop the seeded steer'
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
                # Ownership is registered after the widgets are mounted, so waiting for
                # the text alone can win the race against the assignment. The line being
                # on screen and the app vouching for it are two facts; this waits for the
                # second instead of assuming it.
                await _until(pilot, lambda: app._service_pending, what='ownership')
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
                    pending_reads = transport.pending_reads

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
                    # learned the message by asking the service for the waiting rows
                    # again, on its own stream. A screen that painted the message
                    # locally would satisfy the assertion above and fail this one.
                    assert watcher._client is not None
                    assert watcher._service_following is True
                    assert transport.streams >= 2, 'each terminal follows on a connection of its own'
                    assert transport.pending_reads > pending_reads, 'the watcher re-read the waiting rows'

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


def test_the_end_of_a_turn_paints_the_call_this_screen_never_received(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end of a turn repaints from the rows, so a missed call is not a lost call.

    The report was a second terminal that did not show some of the calls a turn made,
    and only saw them after it was closed and reopened. Nothing in the paint after a
    turn noticed: the only trigger for re-reading the transcript was another client
    accepting a message *during* the turn, so a screen whose stream lost the events of
    the turn itself — a gap, a dropped delta, a result with no block to land in — kept
    the hole until the next manual reload.

    So this terminal's stream never carries the call, its result, or the acceptance
    announcement that would have asked it to repaint mid-turn: the hole the report
    describes, and the case in which nothing but the end of the turn can notice it.
    The tool it never received has to be on screen once ``turn_finished`` is handled,
    painted from the service's own projection, with the arguments only the rows have.
    """
    release = asyncio.Event()
    started = asyncio.Event()
    composition = _composition(tmp_path, monkeypatch, call_tools=('lookup',))

    @composition.agent.tool_plain
    async def lookup() -> str:
        started.set()
        await release.wait()
        return 'found'

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, sending_client):
            deaf = transport.fork(drop=frozenset({'tool_call', 'tool_result', 'state_changed'}))
            watching_client = ServiceClient(SERVICE, transport=deaf)
            try:
                sender = _terminal(sending_client, _session(runtime))
                watcher = _terminal(watching_client, _session(runtime))
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
                    await sender_pilot.press('h', 'i')
                    await sender_pilot.press('enter')

                    # The call runs in the service; the turn is still open, and the
                    # screen whose stream lost the events has nothing for it.
                    await _both_until(pilots, started.is_set, what='the call running in the service')
                    assert watcher._turn_active is True, 'the watcher is inside the same turn'
                    assert _tool_rows(watcher) == [], 'the watcher is missing the call, by construction'
                    reads = deaf.transcript_reads

                    release.set()
                    await _both_until(pilots, lambda: not watcher._turn_active, what='the end of the turn')

                    # Painted from the rows once the stream stopped: the call the events
                    # never carried, and once only.
                    await _both_until(
                        pilots,
                        lambda: [name for name, _ in _tool_rows(watcher)] == ['lookup'],
                        what='the call the rows carry',
                    )
                    assert '› hi' in _transcript(watcher), 'the message of the turn is part of the same rebuild'
                    assert deaf.transcript_reads > reads, 'the end of the turn read the rows again'
                    assert watcher._service_rebuild_due is False, 'the repair is done, not still owed'
            finally:
                await watching_client.aclose()

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
                await _until(pilot, lambda: effect(app, before_transcript), what=f'{name} to take effect')

                assert effect(app, before_transcript), f'{name} ran here but left no visible effect'
                assert len(app.screen_stack) == 1, 'a client command opens no panel it does not need'
                assert _inputs(runtime) == before, 'a client command writes nothing to the queue'

    asyncio.run(inner())


BLOCKED_PROBE = Transport(
    name='/probe',
    owner=CommandOwner.CORE,
    state=TransportState.BLOCKED,
    reason='the service has no endpoint for it, so there is nothing to wire to.',
)
"""A row that exists only in this file, because the table has no such row any more.

Every command the catalogue declares is now served: the eight client ones run here
and the rest are delegated. The refusal stays a live decision all the same — the
table has to be total, and a row that cannot act must answer with one sentence
instead of borrowing another — so the sentence is proven on a row built for it
rather than left to a future regression.
"""

REFUSED: tuple[Transport, ...] = (
    *[row for row in TRANSPORT if row.state in {TransportState.NOT_WIRED, TransportState.BLOCKED}],
    BLOCKED_PROBE,
)
"""The rows with no way out, and the synthetic one the table no longer has.

An empty list here is not an empty test: `parametrize` turns it into a silent
skip, which is how this proof once disappeared. The probe keeps the parameter set
real without a second list that could drift from the table, and the assertion
below is what makes a future empty set fail loudly instead of quietly.
"""

assert REFUSED, 'the refusal test must not become an empty parameter set: pytest skips those silently'


@pytest.mark.parametrize('row', REFUSED, ids=[row.name for row in REFUSED])
def test_a_core_command_says_who_serves_it_and_leaves_the_screen_alone(
    row: Transport, composition: Composition, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A core command this terminal cannot reach is one sentence, not a failure.

    "Exactly one notice" is counted on the blocks the command added, not on the
    transcript as a whole: a picker, a second notice or a repaint all add a block,
    and the command is supposed to add exactly one. `screen_stack` catches the
    panel a handler would have pushed; the durable rows catch the write a handler
    would have made.

    The probe row is injected into the table here, because the answer under test is
    the dispatcher's reading of a row: a synthetic row that never reaches
    `transport_for` would prove the sentence and not the decision.
    """
    name = row.name
    monkeypatch.setitem(command_transport._BY_NAME, name, row)

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

                    # Both events, not just the first. The loop applies one event per
                    # await, so a predicate the first event satisfies returns while the
                    # second is still in the pipe, and the assertion after it races the
                    # stream — which is how this test failed twice in thirty runs. The
                    # fact it wants is "the new run was replayed whole", and both lines
                    # on screen is that fact.
                    await _until(
                        pilot,
                        lambda: (
                            'the new run, event 1' in _transcript(app) and 'the new run, event 2' in _transcript(app)
                        ),
                        what='both events of the new run',
                    )
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
                assert 'A turn is running' in _transcript(app)

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


def test_new_with_a_worktree_cuts_it_in_the_service_and_starts_the_session_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    """`/new --worktree` is the service's tree and the service's session.

    `create_and_switch` moves the *process*, so the directory this test started in
    is restored in a `finally`: a later test that resolved a relative path would
    otherwise inherit a worktree whose teardown has already removed it.
    """
    composition = _composition(tmp_path, monkeypatch, root=repo)
    original = Path.cwd()
    try:

        async def inner() -> None:
            async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the state')
                    before = runtime.session.id

                    await app.handle_command('/new', '--worktree fresh')
                    await pilot.pause()

                    created = Path(os.path.realpath(str(runtime.session.cwd)))
                    assert runtime.session.id != before, 'the session is new'
                    assert created == Path(os.path.realpath(str(runtime.composition.workspace_manager.root)))
                    assert created.name == 'fresh', 'the session runs in the worktree that was cut'
                    assert (created / 'README.md').is_file(), 'and that directory is a real checkout'
                    assert 'Workspace: fresh.' in _transcript(app), 'the move is announced'
                    assert f'New session {runtime.session.short_id}' in _transcript(app)

        asyncio.run(inner())
    finally:
        os.chdir(original)


def test_a_worktree_name_git_refuses_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    """A refused name is one notice: no session, no tree, no move.

    The name is refused by the service, so this also proves the command reports the
    service's own sentence instead of inventing one — and that a refusal leaves the
    repository, the workspace and the queue exactly as they were.
    """
    composition = _composition(tmp_path, monkeypatch, root=repo)
    original = Path.cwd()
    try:

        async def inner() -> None:
            async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the state')
                    before = runtime.session.id
                    workspace = runtime.composition.workspace_manager.current().label
                    inputs = _inputs(runtime)

                    await app.handle_command('/new', '--worktree UPPER_case')
                    await pilot.pause()

                    text = _transcript(app)
                    assert 'Invalid worktree name' in text, "the service's reason reaches the screen"
                    assert 'New session' not in text, 'nothing was announced because nothing happened'
                    assert runtime.session.id == before, 'no session was created'
                    assert runtime.composition.workspace_manager.current().label == workspace
                    assert runtime.composition.root == Path(os.path.realpath(str(repo)))
                    assert _inputs(runtime) == inputs, 'and the queue is untouched'

        asyncio.run(inner())
    finally:
        os.chdir(original)


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


def test_the_restored_prompt_brings_its_image_back_to_the_composer(composition: Composition, tmp_path: Path) -> None:
    """A rollback returns the picture, not a marker pointing at nothing.

    The service re-encodes every image before it stores it, so the bytes on the way
    back are not the bytes that were sent. The comparison is the pixels: a test that
    compared bytes would be measuring the encoder, and would break the day it
    changed its mind about compression.
    """
    from src.ui.widgets.rollback_screen import ROLLBACK_ONLY

    color = (10, 120, 200)

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await client.send_turn(
                TurnRequest(
                    '[Image #1] put this back',
                    images=(OutgoingImage(media_type='image/png', data=base64.b64encode(_png(color)).decode('ascii')),),
                )
            )
            await asyncio.wait_for(runtime.wait(), timeout=20)
            points = (await client.rollback_points())['points']
            assert points

            app = _terminal(client, _session(runtime))
            app.push_screen_wait = _Chooser([SimpleNamespace(turn_id=points[0]['turn_id'], action=ROLLBACK_ONLY)])
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                await app.handle_command('/rollback')
                await pilot.pause()

                composer = app.query_one(PromptInput)
                assert 'put this back' in composer.value, 'the words came back'
                assert '[Image #1]' in composer.value, 'and the marker that stands for the picture'
                assert len(composer.attachments) == 1, 'the picture came back with them'
                attachment = composer.attachments[0]
                assert attachment.media_type == 'image/png'
                with Image.open(io.BytesIO(attachment.data)) as restored:
                    assert restored.size == (4, 4)
                    assert restored.convert('RGB').getpixel((0, 0)) == color, 'the pixels, not the bytes'

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


# --- clicking a message: the seq is the turn ----------------------------------
#
# A click carries the transcript position the line was painted at; a rollback point
# carries a turn id. `/api/rollback` projects `seq` on every point for exactly this
# lookup, so the preselection is a table read in the terminal and not a guess about
# which message opened which turn.


def test_a_clicked_position_names_the_turn_that_opened_it() -> None:
    """The mapping, as a function: no screen, no service, no event loop."""
    rows = (
        {'turn_id': 'turn-second', 'seq': 7},
        {'turn_id': 'turn-first', 'seq': 3},
        {'turn_id': 'turn-without-a-prompt', 'seq': None},
    )

    assert _turn_for_seq(rows, 7) == 'turn-second'
    assert _turn_for_seq(rows, 3) == 'turn-first'
    assert _turn_for_seq(rows, 11) is None, 'a position no point claims preselects nothing'
    assert _turn_for_seq(rows, None) is None, 'a command carries no position at all'
    assert _turn_for_seq(({'turn_id': None, 'seq': 1},), 1) is None, 'a point without a turn id is not usable'


def test_a_clicked_message_preselects_its_turn_in_the_points_list(composition: Composition, tmp_path: Path) -> None:
    """The click opens the list on the row the user pointed at.

    The screen is captured rather than driven: what a key does inside it has its own
    test, and what this proves is that the position the line carries became the turn
    the list starts on — through the message the line posts, not by calling the
    handler with a number the test made up.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await _seed_turn(runtime, client, 'first question')
            await _seed_turn(runtime, client, 'second question')
            points = (await client.rollback_points())['points']
            clicked = next(point for point in points if point['preview'] == 'first question')
            assert isinstance(clicked['seq'], int), 'the projection carries the position a click sends'

            seen: list[Any] = []

            async def capture(screen: Any) -> None:
                seen.append(screen)

            app = _terminal(client, _session(runtime))
            app.push_screen_wait = capture
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: 'first question' in _transcript(app), what='the history')
                painted = [
                    widget
                    for widget in app.query_one(ChatLog).children
                    if getattr(widget, 'seq', None) == clicked['seq']
                ]
                assert painted, 'the position the projection reports is the position the transcript paints'

                painted[0].on_click()
                await _until(pilot, lambda: bool(seen), what='the rollback list')

                assert seen[0]._preselected == clicked['turn_id'], 'the list starts on the clicked turn'

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
                    inputs_before = _inputs(runtime)

                    await pilot.press(key)
                    await _until(pilot, lambda: len(chat.children) > before, what=f'the {key} refusal')

                    assert len(app.screen_stack) == 1, f'{key} opened a panel it cannot complete'
                    assert 'A turn is running' in '\n'.join(
                        _block_text(widget) for widget in list(chat.children)[before:]
                    )
                    assert _inputs(runtime) == inputs_before, f'{key} wrote nothing to the queue'

    asyncio.run(inner())


# --- the state a client must not guess about ----------------------------------
#
# `busy` is the only thing that says whether a turn is running, so the two ways the
# projection can fail to say it are both refusals: an absent field, and a read that
# does not answer. "Probably idle" is exactly the assumption that would swap a
# session out from under a running agent.


class _StateProjection(httpx.AsyncBaseTransport):
    """The real service, with `/api/state` answered by ``respond`` instead.

    Everything else — `/events` included — goes through the transport it wraps, so a
    test that reshapes one field still talks to the same application, the same routes
    and the same bearer check as every other test in this file.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, respond: Callable[[dict[str, Any]], httpx.Response]) -> None:
        self._inner = inner
        self._respond = respond

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if request.url.path != '/api/state':
            return response
        await response.aread()
        return self._respond(json.loads(response.content))


class _StateDiesAfterAttaching(httpx.AsyncBaseTransport):
    """The service until `/api/state` has answered once, then a socket that refuses.

    A terminal attaches to a live service and that route stops answering afterwards.
    Failing the very first read instead would test the attach path rather than the
    guard a typed command runs.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.reads = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/state':
            self.reads += 1
            if self.reads > 1:
                raise httpx.ConnectError('the service stopped answering', request=request)
        return await self._inner.handle_async_request(request)


def _without_busy(state: dict[str, Any]) -> httpx.Response:
    """The projection with the one field removed: a service that cannot say."""
    return httpx.Response(200, json={key: value for key, value in state.items() if key != 'busy'})


def test_a_projection_without_busy_refuses_instead_of_assuming_idle(composition: Composition, tmp_path: Path) -> None:
    """Fail closed: the command does not run on a guess about whether a turn runs.

    The field is absent rather than false — an older service, or a bug — and the
    sentence says exactly that instead of borrowing the busy one, which would assert a
    turn nobody can see.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _ignored):
            without_busy = _StateProjection(transport, _without_busy)
            client = ServiceClient(SERVICE, transport=without_busy)
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the state')
                    chat = app.query_one(ChatLog)
                    before = len(chat.children)
                    inputs_before = _inputs(runtime)
                    agent_before = runtime.session.agent

                    await app.handle_command('/agent', 'reviewer')
                    await pilot.pause()

                    added = list(chat.children)[before:]
                    assert len(added) == 1, '/agent answers in exactly one notice'
                    assert BUSY_UNKNOWN.format(name='/agent') in _block_text(added[0])
                    assert len(app.screen_stack) == 1, 'a command that cannot check opens no panel'
                    assert _inputs(runtime) == inputs_before, 'nothing was written'
                    assert runtime.session.agent == agent_before, 'and nothing was switched'
            finally:
                await client.aclose()

    asyncio.run(inner())


def test_an_unreadable_state_refuses_the_command_instead_of_assuming_idle(
    composition: Composition, tmp_path: Path
) -> None:
    """The other half of fail-closed: no answer is not permission.

    The attach succeeded and the route stopped answering afterwards, which is what the
    morning after a service crash looks like. The command is refused with the failure's
    own sentence — the way out is in it — and the picker never opens.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _ignored):
            client = ServiceClient(SERVICE, transport=_StateDiesAfterAttaching(transport))
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the state')
                    chat = app.query_one(ChatLog)
                    before = len(chat.children)
                    inputs_before = _inputs(runtime)

                    await app.handle_command('/effort')
                    await pilot.pause()

                    added = list(chat.children)[before:]
                    assert len(added) == 1, '/effort answers in exactly one notice'
                    text = _block_text(added[0])
                    assert '✖' in text, 'the notice is a failure, not a silent return'
                    assert 'sigil server' in text, 'and it names the way out'
                    assert len(app.screen_stack) == 1, 'no picker opened on a state that could not be read'
                    assert _inputs(runtime) == inputs_before, 'nothing was written'
            finally:
                await client.aclose()

    asyncio.run(inner())


@pytest.mark.parametrize('name', ('/new', '/load', '/provider'))
def test_a_command_that_swaps_the_session_refuses_when_the_state_cannot_be_read(
    name: str, composition: Composition, tmp_path: Path
) -> None:
    """A command that *changes* the session does not run on a state it could not read.

    `/effort` above cannot show what the guard is worth: it refuses twice, here and in
    its own `is_refused_while_busy`. These three are the ones the guard is the only
    thing standing in front of, and each has its own way of hurting: `/new` creates a
    session, `/load` opens the picker that would replace it, and `/provider` opens the
    accounts screen, whose next keypresses add, rename or delete an account. A guard
    that answered `False` on a failed read would reach the service through routes that
    still answer while `/api/state` does not. That is the mutation this test exists for.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _ignored):
            client = ServiceClient(SERVICE, transport=_StateDiesAfterAttaching(transport))
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the state')
                    chat = app.query_one(ChatLog)
                    before = len(chat.children)
                    inputs_before = _inputs(runtime)
                    service_session = runtime.session.id
                    local_session = app._session.id

                    await app.handle_command(name)
                    await pilot.pause()

                    added = list(chat.children)[before:]
                    # The cost first: a guard that let this through would have created
                    # or opened a conversation, and the notice count would only say the
                    # screen drew two blocks instead of one.
                    assert runtime.session.id == service_session, 'the service kept its session'
                    assert app._session.id == local_session, 'and the reading here was not swapped'
                    assert _inputs(runtime) == inputs_before, f'{name} wrote nothing to the queue'
                    assert len(app.screen_stack) == 1, f'{name} opened what it cannot complete'
                    assert len(added) == 1, f'{name} answers in exactly one notice'
                    assert '✖' in _block_text(added[0]), 'the notice is the failure, not a panel'
            finally:
                await client.aclose()

    asyncio.run(inner())


# --- the tree, through the service -------------------------------------------


def _service_subagent(runtime: ServerRuntime, *, mode: str = 'sync', task: str = 'Review the diff.') -> Any:
    """A node of the service's real tree, hanging from the session agent.

    Added through the tree the summoner owns, which is the object the routes act
    on: a subagent `TestModel` really ran would be finished before the assertion
    read it, and "stop a running node" would be a race instead of a fact.
    """
    return runtime.composition.agent_trees.active.add(
        parent_id=None,
        name='reviewer',
        role=AgentRole.SUBAGENT,
        task=task,
        mode=mode,
        model='test',
    )


async def _open_tree(pilot: Any, app: SigilApp) -> AgentTreeScreen:
    """`ctrl+t`, and the window it opens."""
    await pilot.press('ctrl+t')
    await _until(pilot, lambda: isinstance(app.screen, AgentTreeScreen), what='the tree window')
    return app.screen


def _rows(screen: AgentTreeScreen) -> list[str]:
    """The list as a reader sees it: one string per painted row."""
    return [render_text(option.prompt) for option in screen.query_one(OptionList).options]


def _highlight(screen: AgentTreeScreen, node_id: str) -> None:
    """Puts the cursor on a row by id: the list is ordered by attention, not by birth."""
    options = screen.query_one(OptionList)
    options.highlighted = options.get_option_index(node_id)


def _subagent_text(app: SigilApp) -> str:
    """The transcript of the subagent on screen, read the way it is painted."""
    return '\n'.join(_block_text(widget) for widget in app.query_one(AgentView).transcript.children)


def test_the_projection_becomes_the_nodes_the_widget_paints() -> None:
    """The one place the service's JSON becomes an `AgentNode`.

    The widget is not touched by this cut, so the projection has to arrive as the
    type it already knows: its ids, its tree position, its state, and a clock that
    stops when the service stopped counting.
    """

    nodes = _nodes_from_tree(
        {
            'root_id': 'a',
            'nodes': [
                {
                    'id': 'a',
                    'name': 'wizard',
                    'role': 'agent',
                    'parent_id': None,
                    'depth': 0,
                    'task': '',
                    'status': 'running',
                    'mode': 'sync',
                    'model': 'test',
                    'elapsed': 2.0,
                    'runs': 1,
                    'delivered': True,
                    'budget_exhausted': False,
                },
                {
                    'id': 'b',
                    'name': 'reviewer',
                    'role': 'subagent',
                    'parent_id': 'a',
                    'depth': 1,
                    'task': 'Review the diff.',
                    'status': 'done',
                    'mode': 'async',
                    'model': 'test',
                    'elapsed': 4.5,
                    'runs': 2,
                    'delivered': False,
                    'budget_exhausted': True,
                    'result': 'looks fine',
                },
            ],
        }
    )

    root, child = nodes
    assert root.is_root is True
    assert root.label == 'Wizard'
    assert child.id == 'b'
    assert child.parent_id == 'a'
    assert child.depth == 1
    assert child.status is NodeStatus.DONE
    assert child.mode == 'async'
    assert child.runs == 2
    assert child.delivered is False
    assert child.budget_exhausted is True
    assert child.result == 'looks fine'
    assert child.elapsed == pytest.approx(4.5, abs=0.1), "a finished node's clock does not keep running"


def test_the_tree_opens_when_the_service_has_no_subagents(composition: Composition, tmp_path: Path) -> None:
    """`ctrl+t` always opens, and an empty tree is information, not a refusal.

    The false sentence this replaces was about "this session has no agents": the
    session's agent exists, and the list is where a summon will appear.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                screen = await _open_tree(pilot, app)

                assert 'No subagents' in render_text(screen.query_one('#tree-status').render())
                assert any('appear here' in row for row in _rows(screen))
                assert 'no agents yet' not in _transcript(app)

    asyncio.run(inner())


def test_the_tree_lists_the_subagents_the_service_has(composition: Composition, tmp_path: Path) -> None:
    """The list is the service's tree: the attached terminal holds none of its own."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            node = _service_subagent(runtime, task='read the tree')
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                screen = await _open_tree(pilot, app)
                rows = _rows(screen)

                assert any('Reviewer' in row and node.short_id in row for row in rows)
                assert any('read the tree' in row for row in rows)
                assert '1 subagent' in render_text(screen.query_one('#tree-status').render())

    asyncio.run(inner())


def test_stopping_a_node_from_the_tree_stops_it_in_the_service(composition: Composition, tmp_path: Path) -> None:
    """The key, the list, and the stop landing where the node actually runs."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            node = _service_subagent(runtime)
            tree = runtime.composition.agent_trees.active
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                screen = await _open_tree(pilot, app)
                _highlight(screen, node.id)
                await pilot.press('k')

                await _until(
                    pilot,
                    lambda: tree.node(node.id).status is NodeStatus.CANCELLED,
                    what='the stop at the service',
                )
                screen = await _open_tree(pilot, app)
                assert any('cancelled' in row for row in _rows(screen))

    asyncio.run(inner())


def test_sending_a_node_to_the_background_changes_it_in_the_service(composition: Composition, tmp_path: Path) -> None:
    """`b` is the service's decision: the node keeps working and answers later."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            node = _service_subagent(runtime)
            tree = runtime.composition.agent_trees.active
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await app.run_tree_action(TreeAction(kind='detach', node_id=node.id))

                await _until(pilot, lambda: tree.node(node.id).mode == 'async', what='the background at the service')
                screen = await _open_tree(pilot, app)
                assert any('async' in row for row in _rows(screen))

    asyncio.run(inner())


def test_opening_a_subagent_shows_the_transcript_the_service_keeps(composition: Composition, tmp_path: Path) -> None:
    """`enter` reads the node's own messages, which only the service has."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            node = _service_subagent(runtime, task='read the tree')
            node.messages.append(ModelRequest(parts=[UserPromptPart(content='read the tree')]))
            node.messages.append(ModelResponse(parts=[TextPart('it has three modules')]))
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                screen = await _open_tree(pilot, app)
                _highlight(screen, node.id)
                await pilot.press('enter')

                await _until(pilot, lambda: app.subagent_open, what='the subagent view')
                await _until(
                    pilot,
                    lambda: 'it has three modules' in _subagent_text(app),
                    what='the transcript the service kept',
                )
                assert app.viewed_node_id() == node.id
                assert app.query_one(PromptInput).disabled is True, 'a subagent is read, not talked to'

    asyncio.run(inner())


def test_escape_inside_a_subagent_goes_back_instead_of_offering_to_kill(
    blocked: tuple[Composition, asyncio.Event], tmp_path: Path
) -> None:
    """`Esc` in a node is the way back, even while a turn is running.

    The abort branch used to be asked first and the navigation was gated on no turn
    being active, which is the opposite of the case: a node is usually open while a
    run is in flight, so every press armed the confirmation and the reader could not
    leave. Killing the run is still two `Esc`s, from the conversation.
    """
    held, release = blocked

    async def inner() -> None:
        async with _service_under_test(held, tmp_path) as (runtime, _transport, client):
            await client.send_turn(TurnRequest(text='go'))
            await _wait_until(lambda: runtime.busy, what='the running turn')
            node = _service_subagent(runtime, task='read the tree')
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._turn_active, what='the running turn on screen')
                screen = await _open_tree(pilot, app)
                _highlight(screen, node.id)
                await pilot.press('enter')
                await _until(pilot, lambda: app.subagent_open, what='the subagent view')

                await pilot.press('escape')

                await _until(pilot, lambda: not app.subagent_open, what='the way back')
                assert app._turn_active is True, 'going back does not stop the run'
                assert app._abort_deadline is None, 'the press armed the confirmation'
                assert ABORT_HINT not in app.query_one(StatusBar).render().plain, 'the press asked to kill'
                release.set()

    asyncio.run(inner())


def test_a_tree_change_on_the_service_repaints_the_open_list(composition: Composition, tmp_path: Path) -> None:
    """`tree_changed` refreshes the list, not only the state behind it."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                screen = await _open_tree(pilot, app)
                assert any('appear here' in row for row in _rows(screen))

                node = _service_subagent(runtime)
                # The event the summoner publishes when it adds a node: the bytes the
                # service would send, from the hub the service publishes into.
                runtime.hub.publish('tree_changed', node_id=node.id)

                await _until(
                    pilot,
                    lambda: any('Reviewer' in row for row in _rows(screen)),
                    what='the new row in the open list',
                )

    asyncio.run(inner())


# --- the sidebar, fed by the service projection ------------------------------


def _loaded_skill(name: str = 'clean-code') -> list[ModelRequest | ModelResponse]:
    """The pair of parts that records one successful `load_skill`."""
    return [
        ModelResponse(parts=[ToolCallPart(tool_name='load_skill', args={'name': name}, tool_call_id='load-1')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content='ok', tool_call_id='load-1')]),
    ]


class _StateWithoutSections(httpx.AsyncBaseTransport):
    """The real service, with named sections removed from `/api/state`.

    A service that stops projecting a section is the case the panel has to
    survive: the key is absent, not empty, and the screen must say so instead of
    inventing a zero. Removing keys here is the smallest way to produce that
    reply without teaching the service a test-only mode.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, *sections: str) -> None:
        self._inner = inner
        self._sections = frozenset(sections)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if request.url.path != '/api/state' or not self._sections:
            return response
        body = json.loads(await response.aread())
        for section in self._sections:
            body.pop(section, None)
        return httpx.Response(200, json=body)


def test_the_attached_sidebar_paints_the_context_the_service_measured(composition: Composition, tmp_path: Path) -> None:
    """`/api/context` is the only measurement of this session; the panel shows it.

    The terminal has no history of its own in attached mode, so a percentage
    computed here would be a second opinion about a conversation the service owns.
    """

    async def inner() -> None:
        composition.session.messages.append(
            ModelResponse(
                parts=[TextPart('ok')],
                usage=RequestUsage(input_tokens=30_000, output_tokens=100),
                model_name='claude-sonnet-4-5',
                provider_details={'cost': 0.042},
            )
        )
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            payload = await client.context()
            expected = f'context: {payload["percent"]:.0f}%'
            pair = f'({compact_tokens(payload["used"])} / {compact_tokens(payload["window"])})'
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(120, 40)) as pilot:
                await _until(
                    pilot,
                    lambda: expected in app.query_one(SidebarPanel).text,
                    what='the context the service measured',
                )
                text = app.query_one(SidebarPanel).text
                assert pair in text
                assert 'cost: $0.04' in text

    asyncio.run(inner())


def test_the_attached_sidebar_paints_skills_mcp_and_model_from_the_state(
    composition: Composition, tmp_path: Path
) -> None:
    """Three sections, three projections, none of them read locally.

    The session's transcript is the service's, the MCP layer is the service's and
    the active model is the service's. The panel reads all three from `/api/state`.
    """
    config = tmp_path / 'mcp.json'
    config.write_text(json.dumps({'mcp_servers': {'github': {'command': 'npx'}}}), encoding='utf-8')
    composition.mcp.install(MCPManager(load_config([config], environ={}).servers))

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            runtime.session.messages.extend(_loaded_skill())
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(120, 40)) as pilot:
                await _until(
                    pilot,
                    lambda: 'clean-code' in app.query_one(SidebarPanel).text,
                    what='the skill the transcript recorded',
                )
                await _until(
                    pilot,
                    lambda: 'MCP (1)' in app.query_one(SidebarPanel).text,
                    what='the MCP server the service projects',
                )
                text = app.query_one(SidebarPanel).text
                assert 'github' in text
                assert 'work' in text
                assert 'first/model' in text

    asyncio.run(inner())


def test_a_section_the_service_omits_is_painted_as_unknown(composition: Composition, tmp_path: Path) -> None:
    """An absent section is not an empty one, and not a zero.

    `none yet`, `No tasks yet.` and `not configured` are claims. Without the key
    the panel cannot make any of them, so it names the gap.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _client):
            client = ServiceClient(
                SERVICE,
                transport=_StateWithoutSections(transport, 'model', 'skills', 'mcp', 'roadmap'),
            )
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(120, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the state')
                    await _until(
                        pilot,
                        lambda: app.query_one(SidebarPanel).text.count('the service did not report it') >= 4,
                        what='the four sections the service omitted',
                    )
                    text = app.query_one(SidebarPanel).text
                    assert 'none yet' not in text
                    assert 'No tasks yet.' not in text
                    assert 'not configured' not in text
            finally:
                await client.aclose()

    asyncio.run(inner())


def test_the_attached_sidebar_shows_the_roadmap_the_service_kept(composition: Composition, tmp_path: Path) -> None:
    """The plan is a row of the service's database, not a file this process reads."""
    composition.roadmap_store.root.set_items([TodoItem(title='Ship the panel', status='in_progress')])

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(120, 40)) as pilot:
                await _until(
                    pilot,
                    lambda: 'Ship the panel' in app.query_one(SidebarPanel).text,
                    what='the plan the service kept',
                )
                text = app.query_one(SidebarPanel).text
                assert 'Roadmap' in text
                assert '▶ Ship the panel' in text

    asyncio.run(inner())


def test_state_changed_repaints_the_attached_sidebar(composition: Composition, tmp_path: Path) -> None:
    """The existing event is the refresh: no timer, no polling, no sleep."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(120, 40)) as pilot:
                await _until(
                    pilot,
                    lambda: 'No tasks yet.' in app.query_one(SidebarPanel).text,
                    what='the empty plan at mount',
                )
                composition.roadmap_store.root.set_items([TodoItem(title='Repaint on the event', status='pending')])
                runtime.hub.publish_state('roadmap')

                await _until(
                    pilot,
                    lambda: 'Repaint on the event' in app.query_one(SidebarPanel).text,
                    what='the plan after `state_changed`',
                )

    asyncio.run(inner())


# --- the chrome the service paints -------------------------------------------
#
# An attached terminal composes no `WorkspaceManager` and no model service, so
# every fact about *where* it is running and *what* answers has to come from the
# projection `/api/state` already carries. These tests read those facts from the
# projection, not from a second source the client would have to keep in step.


def test_the_chrome_reads_the_workspace_the_service_reports() -> None:
    """The title, the bar and the worktree badge are one projection.

    A missing report stays empty. Borrowing a local path to fill the gap would be a
    second truth, and the terminal is not the process that decides where the
    conversation runs.
    """

    async def inner() -> None:
        client = ServiceClient(SERVICE, transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        try:
            app = _terminal(client, Session(id='s', cwd='/repo'))
            app._service_state = {
                'workspace': {
                    'root': '/repo/.sigil/worktrees/better-debug',
                    'label': 'better-debug',
                    'branch': 'feat',
                    'is_worktree': True,
                    'repository': 'sigil',
                }
            }
            assert app.repository_label() == 'sigil:better-debug'
            assert app._workspace_root() == '/repo/.sigil/worktrees/better-debug'
            badge = app.workspace_badge()
            assert badge is not None
            assert (badge.label, badge.branch, badge.is_worktree) == ('better-debug', 'feat', True)

            app._service_state = None
            assert app.repository_label() == '', 'no report paints nothing'
            assert app.workspace_badge() is None, 'and invents no workspace'
            assert app._workspace_root() is None
        finally:
            await client.aclose()

    asyncio.run(inner())


def test_attaching_paints_the_repository_the_service_runs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    """The window title and the bar are repainted when the state arrives.

    The workspace is the service's, so the first paint cannot know it. What this
    proves is that applying the state is what fills both halves, instead of a
    `WorkspaceManager` the attached app does not have.
    """

    composition = _composition(tmp_path, monkeypatch, root=repo)

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                await _until(pilot, lambda: repo.name in _status(app), what='the project in the bar')

                workspace = app._service_state['workspace']
                assert app.repository_label() == workspace['repository'] == repo.name
                assert app._workspace_root() == str(repo)
                assert workspace['label'] in _status(app), 'the worktree badge is the service label'
                assert repo.name in app.window_title_text()

    asyncio.run(inner())


def test_an_unreported_image_capability_is_unknown_and_never_yes() -> None:
    """A service that cannot say answers *unknown*, not *yes*.

    The composer refuses the image either way; the difference is the sentence the
    user reads. Before this, an attached terminal read an empty local snapshot,
    treated it as *no model configured* and accepted the image, so the refusal
    arrived from the provider after the turn had already been spent.
    """

    async def inner() -> None:
        client = ServiceClient(SERVICE, transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        try:
            app = _terminal(client, Session(id='s', cwd='/repo'))

            app._service_state = None
            assert app.accepts_images() is None, 'no state is not a yes'
            assert 'cannot tell' in app.images_refusal()

            app._service_state = {'model': {'configured': False, 'images': True, 'model': ''}}
            assert app.accepts_images() is None, 'an unconfigured model knows nothing about images'
            assert 'cannot tell' in app.images_refusal()

            app._service_state = {'model': {'configured': True, 'images': False, 'model': 'text-only'}}
            assert app.accepts_images() is False
            assert 'text-only does not accept images' in app.images_refusal()

            app._service_state = {'model': {'configured': True, 'images': True, 'model': 'vision'}}
            assert app.accepts_images() is True
        finally:
            await client.aclose()

    asyncio.run(inner())


class _RecordingTransport(httpx.AsyncBaseTransport):
    """The service's transport, with every path it was asked for kept."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.paths: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return await self._inner.handle_async_request(request)


@pytest.mark.parametrize('leaving', ['/quit', 'ctrl+q'])
def test_leaving_an_attached_terminal_detaches_instead_of_stopping_the_work(
    leaving: str, blocked: tuple[Composition, asyncio.Event], tmp_path: Path
) -> None:
    """A closed terminal is not a stop, and the work it did not start keeps running.

    The bug this pins: the exit path posted `/api/stop-all`, and `abort` is global to
    the service, so closing one terminal cancelled the turn a user had started from
    another one — and the session was saved at that cancellation, which is why the
    message only then appeared in the transcript. Leaving detaches: the service keeps
    the work, and its idle shutdown is the only thing that ends it (see
    `tests/test_server_idle.py`).

    Both ways out are checked because they are the same promise made twice: the
    `/quit` command and the window's own quit key.
    """

    async def inner() -> None:
        composition, release = blocked
        async with _service_under_test(composition, tmp_path) as (runtime, transport, sending_client):
            # The turn belongs to another terminal: the one that leaves never sent it.
            await sending_client.send_turn(TurnRequest(text='a turn another terminal started'))
            await _wait_until(lambda: runtime.busy, what='the turn the other terminal started')
            recorded = _RecordingTransport(transport)
            client = ServiceClient(SERVICE, transport=recorded)
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._turn_active, what='the running turn on screen')
                    if leaving == 'ctrl+q':
                        await pilot.press('ctrl+q')
                    else:
                        await app.handle_command('/quit')
                    await _until(pilot, lambda: app._exit is True, what='the exit')

                    assert '/api/stop-all' not in recorded.paths, 'leaving asked the service to stop everything'
                    assert '/api/abort' not in recorded.paths, 'leaving stopped the running turn'
                # The screen is gone and the work is not: the service is still running it.
                assert runtime.busy is True, 'the turn another terminal started outlived the screen'
                assert _inputs(runtime)[0]['state'] in {'accepted', 'claimed'}, 'the exit stopped the queue row'
            finally:
                await client.aclose()
                release.set()
                await asyncio.wait_for(runtime.wait(), timeout=20)

    asyncio.run(inner())


def test_a_message_accepted_by_one_terminal_is_waiting_on_the_other(
    blocked: tuple[Composition, asyncio.Event], tmp_path: Path
) -> None:
    """The queue overlay is the service's, so every attached screen paints the same row.

    The user's report was a message accepted in one terminal that never showed on the
    other, which was mid-turn and only said "Thinking". The service announces the
    acceptance — `state_changed` with `reason='queue'` and no text — so the screen that
    did not type the message has to read the rows and paint the waiting line, without
    dropping the answer streaming into the transcript it already holds.
    """

    async def inner() -> None:
        composition, release = blocked
        async with _service_under_test(composition, tmp_path) as (runtime, transport, sending_client):
            await sending_client.send_turn(TurnRequest(text='go'))
            await _wait_until(lambda: runtime.busy, what='the turn the sender started')
            watching_client = ServiceClient(SERVICE, transport=transport)
            try:
                watcher = _terminal(watching_client, _session(runtime))
                async with watcher.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: watcher._turn_active, what='the running turn on the other screen')
                    reads = transport.pending_reads

                    await sending_client.send_turn(TurnRequest(text='queued while the turn runs'))

                    await _until(
                        pilot,
                        lambda: (
                            'queued while the turn runs' in _transcript(watcher)
                            and 'queued for the running turn' in _transcript(watcher)
                        ),
                        what='the waiting line of the other terminal',
                    )
                    assert watcher._turn_active is True, 'the line appeared while the turn was still running'
                    assert transport.pending_reads > reads, 'the line came from the waiting rows, not from this screen'

                    release.set()
                    await _until(pilot, lambda: not watcher._turn_active, what='the end of the turn')
                    assert _transcript(watcher).count('queued while the turn runs') == 1, (
                        'the waiting line is history once'
                    )
            finally:
                await watching_client.aclose()

    asyncio.run(inner())


def test_a_queue_event_mid_turn_reads_only_the_waiting_rows(
    blocked: tuple[Composition, asyncio.Event], tmp_path: Path
) -> None:
    """A queue event asks for the wait, not for the conversation already on screen.

    The overlay needs the rows that are waiting; the transcript is being streamed
    into the screen and does not need to be decoded again to learn of one queued
    message. Asking the durable read for it projected the whole session — 231 ms on
    a real one — on the loop that also carries the stream, once per queue event.
    """

    async def inner() -> None:
        composition, release = blocked
        async with _service_under_test(composition, tmp_path) as (runtime, transport, sending_client):
            await sending_client.send_turn(TurnRequest(text='go'))
            await _wait_until(lambda: runtime.busy, what='the turn the sender started')
            watching_client = ServiceClient(SERVICE, transport=transport)
            try:
                watcher = _terminal(watching_client, _session(runtime))
                async with watcher.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: watcher._turn_active, what='the running turn on the other screen')
                    durable_reads = transport.durable_reads
                    pending_reads = transport.pending_reads

                    await sending_client.send_turn(TurnRequest(text='queued while the turn runs'))

                    await _until(
                        pilot,
                        lambda: 'queued while the turn runs' in _transcript(watcher),
                        what='the waiting line of the other terminal',
                    )
                    assert watcher._turn_active is True, 'the turn had already stopped'
                    assert transport.pending_reads > pending_reads, 'the wait was not read from its own route'
                    assert transport.durable_reads == durable_reads, (
                        'a queue event mid-turn re-read the whole durable session'
                    )

                    release.set()
                    await _until(pilot, lambda: not watcher._turn_active, what='the end of the turn')
                    assert _transcript(watcher).count('queued while the turn runs') == 1, (
                        'the waiting line is history once'
                    )
            finally:
                await watching_client.aclose()

    asyncio.run(inner())


def test_a_burst_of_queue_events_reads_the_wait_once(
    blocked: tuple[Composition, asyncio.Event], tmp_path: Path
) -> None:
    """N announcements cost at most one read and one paint, not N of each.

    A queue event carries no text: the rows do. Reading once per event turns a busy
    queue into a burst of requests on the loop that is also painting the stream. A
    read in flight only marks that another is due, and the rows it returns paint the
    whole burst. The gate holds the first read open so the burst is a fact and not a
    race, and the state reads that land while it waits are the proof the loop is free.
    """

    async def inner() -> None:
        composition, release = blocked
        async with _service_under_test(composition, tmp_path) as (runtime, transport, sending_client):
            await sending_client.send_turn(TurnRequest(text='go'))
            await _wait_until(lambda: runtime.busy, what='the turn the sender started')
            watching_client = ServiceClient(SERVICE, transport=transport)
            try:
                watcher = _terminal(watching_client, _session(runtime))
                async with watcher.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: watcher._turn_active, what='the running turn on the other screen')
                    gate = asyncio.Event()
                    transport.pending_gate = gate

                    # The acceptance the sender typed is the first queue event, and its
                    # read is held open here.
                    await sending_client.send_turn(TurnRequest(text='queued while the turn runs'))
                    await _wait_until(lambda: transport.pending_in_flight == 1, what='the first queue read')
                    reads = transport.pending_reads
                    states = transport.state_reads

                    # Four more announcements: the burst a busy queue produces.
                    for _ in range(4):
                        runtime.hub.publish_state('queue')

                    # The stream keeps being applied while the read is still in flight.
                    await _wait_until(
                        lambda: transport.state_reads >= states + 4,
                        what='the burst applied while the read was in flight',
                    )
                    assert transport.pending_in_flight == 1, 'a second read started while the first was open'
                    gate.set()

                    await _until(
                        pilot,
                        lambda: 'queued while the turn runs' in _transcript(watcher),
                        what='the waiting line of the other terminal',
                    )
                    assert transport.pending_reads - reads <= 1, (
                        f'{transport.pending_reads - reads} reads for one burst of five events'
                    )
                    assert _transcript(watcher).count('queued while the turn runs') == 1, (
                        'the row painted once per event'
                    )

                    release.set()
                    await _until(pilot, lambda: not watcher._turn_active, what='the end of the turn')
            finally:
                transport.pending_gate = None
                await watching_client.aclose()

    asyncio.run(inner())


RESTARTED = Endpoint(host='restarted.test', port=80, pid=0, token=TOKEN, started_at=0.0)
"""The service that replaced the first run: another address, its own id space."""


def test_a_restart_does_not_suppress_a_new_question_that_reuses_the_id(
    composition: Composition, tmp_path: Path
) -> None:
    """Question ids are per run, so the answered set must not outlive one.

    A service that restarts numbers its questions from `q1` again. Keeping the id
    the old run answered suppresses a brand-new question that happens to share the
    number, and the turn then waits for an answer no screen will ever offer.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _attached):
            question = {
                'id': 'q1',
                'questions': [
                    {
                        'question': 'Which way?',
                        'allow_custom': False,
                        'options': [
                            {'label': 'Recommended', 'description': 'the short one', 'recommended': True},
                            {'label': 'Other way', 'description': None, 'recommended': False},
                        ],
                    }
                ],
            }

            def with_question(state: dict[str, Any]) -> httpx.Response:
                state['question'] = question
                return httpx.Response(200, json=state)

            scripted = _StateProjection(transport, with_question)
            client = ServiceClient(SERVICE, transport=scripted, launch=lambda **_: Launch(endpoint=RESTARTED))
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app.query_one(QuestionPanel).display, what='the first question')
                    await pilot.press('enter')
                    await _until(pilot, lambda: 'q1' in app._service_answered, what='the answer recorded')
                    await _until(pilot, lambda: not app.query_one(QuestionPanel).display, what='the panel closing')

                    await app.resume_after_restart()

                    await _until(pilot, lambda: app.query_one(QuestionPanel).display, what='the new question')
                    assert app._service_question == 'q1', 'the new run names its question from one again'
                    assert 'q1' not in app._service_answered, 'the answered set belongs to the old run'
            finally:
                await client.aclose()

    asyncio.run(inner())


# --- what this process measures, and what only the service does --------------
#
# An attached terminal paints a session it does not run. Every number it shows
# comes from the service's own projection, and what the service does not project
# is named as unmeasured instead of printed as a zero: a confident `0` about a
# conversation this process has never seen is the same lie as a stale one.


def _debug_body(app: SigilApp) -> str:
    """The debug view's body, read the way it is painted."""
    return render_text(app.query_one(AgentDebugView)._body.content)


def _measured_response(text: str, input_tokens: int) -> ModelResponse:
    """One response with reported usage: what `/api/context` measures a session by."""
    return ModelResponse(
        parts=[TextPart(text)],
        usage=RequestUsage(input_tokens=input_tokens, output_tokens=10),
        model_name='claude-sonnet-4-5',
    )


def test_debug_text_in_an_attached_terminal_reads_the_service(composition: Composition, tmp_path: Path) -> None:
    """`/debug --text` reports the projection, and says what nobody measures here.

    The report used to be built from this process's meter and history. A terminal
    that owns neither printed `measured 0`, `projected 0`, `log 0` and `model: ?`
    about a session the service had been running all along: four confident zeros
    where the honest answer is "the service owns the turn".
    """

    async def inner() -> None:
        composition.session.messages.append(_measured_response('ok', 30_000))
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(120, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                report = await app.show_debug_text()
                context = await client.context()

                assert context['used'], 'the service measured this session'
                assert 'measured: 0' not in report, 'a zero this process never measured'
                assert 'model: ?' not in report, 'the service reports the model it runs'
                assert 'not measured in this process — the service owns the turn' in report
                assert 'first/model' in report
                assert f'{context["used"]:,}' in report
                assert f'{context["window"]:,}' in report

    asyncio.run(inner())


def test_the_attached_debug_view_repaints_on_a_service_event(composition: Composition, tmp_path: Path) -> None:
    """`alt+d` follows the turn through the service's events, not a local run.

    The in-process view repaints from the run inside this process. An attached
    terminal has no run, so the events the service publishes are the refresh —
    otherwise the view freezes on whatever the first read happened to say.
    """

    async def inner() -> None:
        composition.session.messages.append(_measured_response('ok', 30_000))
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(120, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                await pilot.press('alt+d')
                await _until(
                    pilot,
                    lambda: 'the service owns the turn' in _debug_body(app),
                    what='the report the service feeds',
                )
                first = _debug_body(app)

                composition.session.messages.append(_measured_response('more', 50_000))
                context = await client.context()
                used = f'{context["used"]:,}'
                assert used not in first, 'the first paint is the projection as of then'

                runtime.hub.publish_state('test')

                await _until(pilot, lambda: used in _debug_body(app), what='the repaint on the event')

    asyncio.run(inner())


def test_new_forgets_the_questions_the_previous_session_answered(composition: Composition, tmp_path: Path) -> None:
    """The answered set belongs to one session, like the question ids belong to one run.

    A new session numbers its questions from one again. Keeping the answered ids is
    a screen that never offers `q1` again: the turn waits for an answer nobody will
    be asked to give.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _attached):
            question = {
                'id': 'q1',
                'questions': [
                    {
                        'question': 'Which way?',
                        'allow_custom': False,
                        'options': [
                            {'label': 'Recommended', 'description': 'the short one', 'recommended': True},
                            {'label': 'Other way', 'description': None, 'recommended': False},
                        ],
                    }
                ],
            }

            def with_question(state: dict[str, Any]) -> httpx.Response:
                state['question'] = question
                return httpx.Response(200, json=state)

            client = ServiceClient(SERVICE, transport=_StateProjection(transport, with_question))
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app.query_one(QuestionPanel).display, what='the question')
                    await pilot.press('enter')
                    await _until(pilot, lambda: 'q1' in app._service_answered, what='the answer recorded')
                    await _until(pilot, lambda: not app.query_one(QuestionPanel).display, what='the panel closing')

                    await app.handle_command('/new')

                    await _until(pilot, lambda: app.query_one(QuestionPanel).display, what='the new session question')
                    assert app._service_answered == set(), 'the answered set belonged to the session that ended'
            finally:
                await client.aclose()

    asyncio.run(inner())


def test_the_attached_panel_names_the_session_as_what_it_describes(composition: Composition, tmp_path: Path) -> None:
    """The panel's sections are the session's, and the line says so.

    The service projects one model, one context and one skill set per session; it
    projects none of them per node. Naming the open subagent over those numbers is
    the panel claiming a per-node measurement it does not have, so the subject line
    names the session and the window title keeps naming the node.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            runtime.session.messages.extend(_loaded_skill())
            node = _service_subagent(runtime, task='read the tree')
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(120, 40)) as pilot:
                await _until(
                    pilot,
                    lambda: 'clean-code' in app.query_one(SidebarPanel).text,
                    what='the skills of the session',
                )

                await app.goto_node(node.id)

                await _until(pilot, lambda: app.subagent_open, what='the subagent view')
                await _until(
                    pilot,
                    lambda: 'session' in app.query_one(SidebarPanel).text,
                    what='the subject of the panel',
                )
                text = app.query_one(SidebarPanel).text
                assert f'{node.label} #{node.short_id}' not in text, 'the panel does not name the node'
                assert 'clean-code' in text, 'the sessions skills stay, now that they say whose they are'

    asyncio.run(inner())


def test_a_message_typed_in_an_attached_terminal_carries_no_local_position(
    composition: Composition, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One message, one position: the service assigns it, this process does not.

    The line used to be painted with `len(_history)`, which is always zero here, so
    a click on any message preselected the first rollback point. A position nobody
    can know is no position: the line stays inert until the transcript is re-read
    from the rows, which is where the real one comes from.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            positions: list[int | None] = []
            typed: list[Any] = []
            original = ChatLog.add_user

            async def spy(self: ChatLog, text: str, **kwargs: Any) -> Any:
                positions.append(kwargs.get('seq'))
                widget = await original(self, text, **kwargs)
                typed.append(widget)
                return widget

            monkeypatch.setattr(ChatLog, 'add_user', spy)
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                app.query_one(PromptInput).value = 'hello there'
                await pilot.press('enter')
                await _until(pilot, lambda: bool(positions), what='the typed line')

                assert positions[0] is None, 'a position this process cannot know is not zero'

                typed[0].on_click()
                await pilot.pause()
                assert not isinstance(app.screen, RollbackScreen), 'a line with no position opens nothing'

    asyncio.run(inner())


def test_the_attached_turn_clock_ticks_while_the_service_runs_one(
    composition: Composition, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The elapsed time is the turn's, and the turn is the service's.

    In process the run schedules the tick that repaints the clock. Attached, no run
    exists here, so the spinner used to sit on `0s` for the whole turn while the
    service worked.
    """

    async def inner() -> None:
        clock = {'now': 1000.0}
        monkeypatch.setattr('src.ui.activity_mixin.time', SimpleNamespace(monotonic=lambda: clock['now']))
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                assert app._client_activity_clock is None, 'an idle terminal ticks nothing'

                app.set_activity(running=True, label='Thinking')
                assert app._client_activity_clock is not None, 'an attached turn needs a clock of its own'

                clock['now'] += 5
                app.activity_tick()
                assert '5s' in _status(app)

                app.set_activity(running=False)
                assert app._client_activity_clock is None, 'the clock goes when the turn does'

    asyncio.run(inner())


# --- the attached coverage the main-features audit found missing -------------
#
# Each of these is about a fact only an attached terminal can be wrong about: a row
# the service painted, the tree the service projected, the mark a session does or
# does not deserve, and a turn the service started on its own.


def _heading_color(app: SigilApp, block: Any) -> Any:
    """The foreground the block paints its Markdown heading in, as rendered."""
    segments = list(app.console.render(block._build(), app.console.options.update_width(80)))
    return next(
        segment.style.color
        for segment in segments
        if 'Heading' in segment.text and segment.style and segment.style.color
    )


def test_a_row_the_service_painted_wears_the_theme_and_follows_a_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row born from the projection is themed like one typed here, and repaints.

    The block is built by `render_items` from the service's text, so it reaches the
    same `AssistantBlock` a live turn builds — and a theme switch has to reach it the
    same way `ChatLog.repaint` reaches the rest. A row that kept the old palette after
    `/theme` would be a second rendering path nobody had checked.
    """
    from rich.color import Color

    from src.ui.widgets.chat_log import AssistantBlock

    composition = _composition(tmp_path, monkeypatch, output='## Heading\n\nbody')

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')
                await client.send_turn(TurnRequest(text='paint me'))
                await _until(pilot, lambda: 'Heading' in _transcript(app), what='the painted answer')

                block = app.query_one(ChatLog).query(AssistantBlock)[0]
                assert _heading_color(app, block) == Color.parse(app.theme_variables['primary'])

                await app.handle_command('/theme', 'synthwave')
                await _until(pilot, lambda: app.theme == 'synthwave', what='the new theme')

                assert _heading_color(app, block) == Color.parse(app.theme_variables['primary'])

    asyncio.run(inner())


def test_the_mark_belongs_to_a_session_the_service_has_not_written_a_word_in(
    composition: Composition, tmp_path: Path
) -> None:
    """Attaching to a stored conversation shows it; attaching to a draft shows the mark.

    The two halves are the same decision seen from both sides, which is why they are
    one test: `_empty` is what the transcript paints behind its children, and the
    service's own durable read is the only thing allowed to clear it.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, client):
            draft = _terminal(client, _session(runtime))
            async with draft.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: draft._service_state is not None, what='the state')
                chat = draft.query_one(ChatLog)
                assert chat._empty is True, 'a draft session is still empty'
                assert chat.render().plain.strip(), 'and the mark is what it draws'

            await _seed_turn(runtime, client, 'a stored message')

            watcher = ServiceClient(SERVICE, transport=transport)
            try:
                stored = _terminal(watcher, _session(runtime))
                async with stored.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: 'a stored message' in _transcript(stored), what='the history')
                    assert stored.query_one(ChatLog)._empty is False, 'a painted conversation retires the mark'
            finally:
                await watcher.aclose()

    asyncio.run(inner())


def test_the_attached_defaults_screen_paints_what_the_service_reads(composition: Composition, tmp_path: Path) -> None:
    """`/default-models` attached opens over the projection, never over empty rows.

    The file is written before the terminal attaches and nothing in this process has
    read it: the entry on the list, the destination line and the installed profiles can
    only have come from `GET /api/model-defaults` and `/api/state`. A row reading `not
    set` here would be the client answering from a state it does not hold, and a
    profile missing from the list is exactly the default the user came to set.
    """
    from src.engine.agents.model_defaults import ConfigScope
    from src.ui.widgets.model_defaults_screen import ModelDefaultsScreen

    config = Path(os.environ['SIGIL_CONFIG'])
    config.write_text(
        json.dumps({'default_model': {'account': 'work', 'model': 'first/model', 'effort': 'high'}}),
        encoding='utf-8',
    )

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                _submit(app, '/default-models')
                await _until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen), what='the defaults screen')
                screen = app.screen
                assert isinstance(screen, ModelDefaultsScreen)
                rows = {row.target: row for row in screen.rows}

                # The entry is the service's: this process never opened that file.
                assert rows[None].summary == 'work · first/model · high'
                assert rows[None].sources == frozenset({ConfigScope.GLOBAL})
                assert rows[None].invalid is False
                # The profile comes from `/api/state`: no file mentions it, and it is
                # the row the user opens the screen to fill in.
                assert rows['reviewer'].installed is True
                assert rows['reviewer'].summary == 'not set'
                # The destination line names the file the service resolved.
                status = render_text(screen.query_one('#defaults-status').render())
                assert 'SIGIL_CONFIG' in status and str(config) in status
                assert transport.defaults_writes == [], 'reading the list writes nothing'

    asyncio.run(inner())


def test_a_default_written_from_an_attached_terminal_goes_through_the_service(
    composition: Composition, tmp_path: Path
) -> None:
    """The write is the POST, and the file that changes is the service's own.

    The terminal holds no writer in this shape — `save_model_default` is not passed to
    an attached app at all — so the file changing is proof the route ran, and the
    notice naming it is proof the screen reports the projection the route answered
    with instead of a sentence it made up.
    """
    from src.ui.widgets.effort_screen import EffortScreen
    from src.ui.widgets.grouped_model_screen import GroupedModelScreen
    from src.ui.widgets.model_defaults_screen import ModelDefaultsScreen

    config = Path(os.environ['SIGIL_CONFIG'])

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                _submit(app, '/default-models')
                await _until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen), what='the defaults screen')
                await pilot.press('enter')
                await _until(pilot, lambda: isinstance(app.screen, GroupedModelScreen), what='the model picker')
                await pilot.press('enter')
                await _until(pilot, lambda: isinstance(app.screen, EffortScreen), what='the effort picker')
                await pilot.press('enter')
                await _until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen), what='the list again')
                await pilot.press('escape')
                await _until(pilot, lambda: not isinstance(app.screen, ModelDefaultsScreen), what='the list closing')
                notices = _transcript(app)

        assert len(transport.defaults_writes) == 1, 'one press, one write, and it travelled'
        sent = transport.defaults_writes[0]
        assert sent['target'] is None and sent['scope'] == 'global'
        assert sent['config'] == {'account': 'work', 'model': 'first/model', 'effort': 'default'}
        # The file is the one the service wrote; the notice reports what it answered.
        assert json.loads(config.read_text(encoding='utf-8'))['default_model'] == sent['config']
        assert 'first/model' in notices
        assert str(config) in notices, 'the notice names the file the projection named'

    asyncio.run(inner())


def test_a_default_the_service_refuses_says_so_and_changes_nothing(composition: Composition, tmp_path: Path) -> None:
    """A turn that starts while the list is open: the write is refused, not applied.

    The dispatcher lets the command through because the session was idle when it was
    typed, which is the case only a real turn can produce. The service — the only
    writer — answers `409` with the engine's sentence, and the file it would have
    rewritten is unchanged: there is no second copy here that could disagree with it.
    """
    from src.ui.widgets.effort_screen import EffortScreen
    from src.ui.widgets.grouped_model_screen import GroupedModelScreen
    from src.ui.widgets.model_defaults_screen import ModelDefaultsScreen

    config = Path(os.environ['SIGIL_CONFIG'])
    before = config.read_text(encoding='utf-8')

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, client):
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                _submit(app, '/default-models')
                await _until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen), what='the defaults screen')
                await pilot.press('enter')
                await _until(pilot, lambda: isinstance(app.screen, GroupedModelScreen), what='the model picker')
                await pilot.press('enter')
                await _until(pilot, lambda: isinstance(app.screen, EffortScreen), what='the effort picker')

                # The turn starts between choosing the effort and writing it.
                async with _busy(runtime):
                    await pilot.press('enter')
                    await _until(pilot, lambda: 'A turn is running' in _transcript(app), what='the refusal')

                assert not isinstance(app.screen, ModelDefaultsScreen), 'a refusal opens no list to trap the user'
                assert len(transport.defaults_writes) == 1, 'the write was attempted and refused'

        assert json.loads(config.read_text(encoding='utf-8')) == json.loads(before), 'nothing was written'

    asyncio.run(inner())


def test_a_turn_the_service_wakes_is_painted_once_in_the_terminal(composition: Composition, tmp_path: Path) -> None:
    """A subagent's report starts a turn on the service; the screen paints it once.

    The wake is the service's own decision — the consumer starts the turn — so what the
    terminal may not do is paint it twice: once from the queue event and once from the
    repaint that follows the end of the turn. Counting the report and the answer is how
    a doubled paint shows up, and each is counted where the reader would see it.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            node = _service_subagent(runtime, mode='async', task='read the tree')
            # The consumer is what decides a report wakes a turn, and it is started by
            # a request in every other test. Here the report is the first thing to
            # happen, so `ensure_workers` is the seam that makes the wake possible at
            # all — without it the event is set with nobody parked on it.
            runtime.ensure_workers()
            app = _terminal(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                await _until(pilot, lambda: app._service_state is not None, what='the state')

                node.finish('The tree reads bottom-up.')
                runtime.composition.summoner._deliver(node, 'The tree reads bottom-up.')

                await _until(pilot, lambda: 'done' in _transcript(app), what='the woken answer')
                await _until(pilot, lambda: not app._turn_active, what='the end of the woken turn')

                text = _transcript(app)
                assert text.count('sent a report.') == 1, 'the report was painted twice'
                # The report is a card that opens its execution, not the squad's words
                # in the reader's mouth: the envelope's body is not a message here.
                assert 'The tree reads bottom-up.' not in text, 'a report is not a user message'
                assert text.count('done') == 1, 'the answer was painted twice'

    asyncio.run(inner())


# --- the window: a conversation is painted a page at a time -------------------
#
# The route pages the transcript and carries the queue on its newest page; these
# tests drive the terminal through it. The conversation is seeded straight into the
# session the service projects, because what is under test is how much of it is
# mounted and where — running three hundred turns through the engine would measure
# the model, not the window.


def _grow_session(runtime: ServerRuntime, turns: int) -> None:
    """`turns` exchanges, straight into the conversation the service projects.

    Two items per turn — a prompt and its answer — so a turn count reads as twice as
    many items in the transcript the route pages.
    """
    messages = runtime.session.messages
    for index in range(turns):
        messages.append(ModelRequest(parts=[UserPromptPart(content=f'question {index:03d}')], run_id=f'q{index}'))
        messages.append(ModelResponse(parts=[TextPart(content=f'answer {index:03d}')], run_id=f'a{index}'))


def _grow_uneven_session(runtime: ServerRuntime, turns: int) -> None:
    """`turns` exchanges whose items outnumber the widgets they paint.

    Every answer carries a thought, so the thought and the answer share one block; every
    fifth turn also runs a tool, so the call and its result share one row. A page of this
    conversation therefore stands for more items than the screen holds rows — the shape
    plain question-and-answer pairs can never produce, and the shape a cursor counted in
    widgets gets wrong.
    """
    messages = runtime.session.messages
    for index in range(turns):
        messages.append(ModelRequest(parts=[UserPromptPart(content=f'question {index:03d}')], run_id=f'q{index}'))
        if index % 5 == 4:
            messages.append(
                ModelResponse(
                    parts=[
                        ThinkingPart(content=f'thought {index:03d}'),
                        ToolCallPart(tool_name='read_file', args={'path': 'notes.md'}, tool_call_id=f'call-{index}'),
                    ],
                    run_id=f'a{index}',
                )
            )
            messages.append(
                ModelRequest(
                    parts=[ToolReturnPart(tool_name='read_file', content='ok', tool_call_id=f'call-{index}')],
                    run_id=f'r{index}',
                )
            )
            messages.append(ModelResponse(parts=[TextPart(content=f'answer {index:03d}')], run_id=f'b{index}'))
            continue
        messages.append(
            ModelResponse(
                parts=[ThinkingPart(content=f'thought {index:03d}'), TextPart(content=f'answer {index:03d}')],
                run_id=f'a{index}',
            )
        )


def _windows(client: ServiceClient) -> list[dict[str, Any]]:
    """Records the pages the screen asks for, so a test can read the cursor it used.

    The request is the contract: a window is proven by what was sent, not by what the
    screen happens to show, and a page that was never asked for is the failure this
    cut exists to prevent.
    """
    reads: list[dict[str, Any]] = []
    original = client.transcript

    async def record(**kwargs: Any) -> dict[str, Any]:
        reads.append(kwargs)
        return await original(**kwargs)

    client.transcript = record  # type: ignore[method-assign]
    return reads


def _windowed(client: ServiceClient, session: Session, window: int = DEFAULT_TRANSCRIPT_WINDOW) -> SigilApp:
    """A terminal whose window is the test's choice, not the machine's."""
    return _terminal(client, session, preferences=Preferences(transcript_window=window))


def test_attaching_to_a_long_session_mounts_only_the_newest_window(composition: Composition, tmp_path: Path) -> None:
    """Five hundred items, one window on screen, and the request says so.

    The count is the assertion: a widget per item of the conversation is what this cut
    removes, and a page that was never asked for cannot be a page that was mounted.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 250)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: 'question 249' in _transcript(app), what='the newest window')

                assert len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, 'the window is what is mounted'
                assert 'question 000' not in _transcript(app), 'the rest of the conversation is not'
                assert reads == [{'limit': DEFAULT_TRANSCRIPT_WINDOW}], 'the attach asks for a page'
                assert chat.has_older is True

    asyncio.run(inner())


def test_reaching_the_top_loads_the_page_above_without_moving_the_reader(
    composition: Composition, tmp_path: Path
) -> None:
    """The cursor is the page's own start, and the row being read stays put.

    Both halves matter: a page fetched from the wrong position leaves a hole or a
    repeat, and a page inserted above a reader who is scrolled up drags the screen
    with it.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 250)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, what='the window')

                chat.scroll_to(y=0, animate=False, immediate=True)
                await pilot.pause()
                watched = chat.children[0]
                row = watched.region.y

                await _until(pilot, lambda: len(chat.children) == 2 * DEFAULT_TRANSCRIPT_WINDOW, what='the page above')
                await _until(pilot, lambda: watched.region.y == row, what='the reading position')

                assert reads[-1] == {'limit': DEFAULT_TRANSCRIPT_WINDOW, 'before': 400}, 'the cursor is where it starts'
                assert 'question 150' in _transcript(app), 'the page above is painted'
                assert chat.older_cursor == 300, 'and the window now starts where that page does'

    asyncio.run(inner())


def test_a_second_trip_to_the_top_loads_another_page(composition: Composition, tmp_path: Path) -> None:
    """Walking the cursor back is a chain, not a single read."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 250)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, what='the window')

                chat.scroll_to(y=0, animate=False, immediate=True)
                await _until(pilot, lambda: len(chat.children) == 2 * DEFAULT_TRANSCRIPT_WINDOW, what='the first page')
                # The reader's row is restored a refresh after the insertion; scrolling
                # before that changes nothing and asks for nothing.
                await _until(pilot, lambda: chat.scroll_offset.y > 0, what='the restored reading position')
                chat.scroll_to(y=0, animate=False, immediate=True)
                await _until(pilot, lambda: len(chat.children) == 3 * DEFAULT_TRANSCRIPT_WINDOW, what='the second page')

                assert [read.get('before') for read in reads] == [None, 400, 300]

    asyncio.run(inner())


def test_the_window_stays_bounded_and_never_drops_the_block_being_written(
    composition: Composition, tmp_path: Path
) -> None:
    """Past the bound, the page furthest from the reader goes — never the live block.

    With the reader at the top that page is the newest one, so the window stops ending
    where the conversation ends; when they come back down the newest page is read
    again. The block being streamed into belongs to no page, so it is not a candidate
    for eviction and neither letting a page go nor reading the newest one back takes
    it off the screen.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 250)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, what='the window')
                await chat.begin_response()
                await chat.add_text('still being written')
                await pilot.pause()
                writing = chat._current
                assert writing is not None

                for before in (400, 300, 200):
                    chat.scroll_to(y=0, animate=False, immediate=True)
                    await _until(
                        pilot, lambda before=before: reads[-1].get('before') == before, what=f'the page before {before}'
                    )
                    await pilot.pause()
                    await pilot.pause()
                    assert len(chat.children) <= 3 * DEFAULT_TRANSCRIPT_WINDOW + 1, 'the window is bounded'
                    assert writing in chat.children, 'the block being written is still mounted'

                assert 'still being written' in _transcript(app)
                assert 'question 249' not in _transcript(app), (
                    'the page furthest from a reader at the top is the one that left'
                )

                # Back at the bottom the newest page is read again; the live block is
                # not a page and is still the same object being written into.
                chat.scroll_end(animate=False)
                await _until(
                    pilot, lambda: 'question 249' in _transcript(app), what='the end of the conversation again'
                )
                assert chat._current is writing, 'the reload does not start a second block'
                assert writing in chat.children, 'and does not take the live one down'
                assert 'still being written' in _transcript(app)
                assert len(chat.children) <= DEFAULT_TRANSCRIPT_WINDOW + 1, 'the window is one page and the live block'

    asyncio.run(inner())


def test_a_reader_at_the_top_lets_go_of_the_newest_page(composition: Composition, tmp_path: Path) -> None:
    """Distance, not age: a reader at the top is furthest from the newest page.

    Three trips up mount one page over the bound. The page that leaves is the one the
    reader is furthest from — the newest — and because it is the page that held the end
    of the conversation, the window no longer ends there.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 250)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, what='the window')

                for before in (400, 300, 200):
                    chat.scroll_to(y=0, animate=False, immediate=True)
                    await _until(
                        pilot, lambda before=before: reads[-1].get('before') == before, what=f'the page before {before}'
                    )
                    await _until(pilot, lambda: chat.scroll_offset.y > 0, what='the restored reading position')

                assert [read.get('before') for read in reads] == [None, 400, 300, 200]
                assert chat._reaches_end is False, 'the newest page left, so the window no longer ends there'
                assert 'question 249' not in _transcript(app), (
                    'the newest page is what a reader at the top is furthest from'
                )
                assert 'question 100' in _transcript(app), 'the page the reader is on stays'

    asyncio.run(inner())


def test_reaching_the_bottom_reads_the_newest_page_once(composition: Composition, tmp_path: Path) -> None:
    """What left the bottom has no cursor to come back with, so it is read again.

    The route pages backwards only, so the repair for a window that let the end go is
    the read the screen opened with. Once per arrival: a reader who keeps scrolling at
    the bottom asks for nothing more, because the window ends there again.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 250)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, what='the window')
                for before in (400, 300, 200):
                    chat.scroll_to(y=0, animate=False, immediate=True)
                    await _until(
                        pilot, lambda before=before: reads[-1].get('before') == before, what=f'the page before {before}'
                    )
                    await _until(pilot, lambda: chat.scroll_offset.y > 0, what='the restored reading position')
                assert chat._reaches_end is False

                asked = len(reads)
                chat.scroll_end(animate=False)
                await _until(pilot, lambda: len(reads) == asked + 1, what='the newest page again')
                await _until(pilot, lambda: 'question 249' in _transcript(app), what='the end of the conversation')

                assert reads[-1] == {'limit': DEFAULT_TRANSCRIPT_WINDOW}, 'the read is the one the screen opened with'
                assert chat._reaches_end is True, 'the window ends where the conversation ends again'
                assert len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, 'the newest page is the window again'

                # A reader who keeps scrolling at the bottom asks for nothing more.
                chat.scroll_to(y=chat.max_scroll_y - 1, animate=False, immediate=True)
                await pilot.pause()
                chat.scroll_end(animate=False)
                await pilot.pause()
                await pilot.pause()

                assert len(reads) == asked + 1, 'one read per arrival, not one per scroll event'

    asyncio.run(inner())


def test_a_reader_near_the_bottom_gives_up_the_page_at_the_top(composition: Composition, tmp_path: Path) -> None:
    """The page being read is never the one let go of while another is further away.

    A page above can arrive while the reader is at the bottom of the window, and then
    the newest page is the one they are inside. What is furthest from them is the top,
    which the cursor can still ask for again, so that is the page that leaves.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 250)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat.children) == DEFAULT_TRANSCRIPT_WINDOW, what='the window')
                for before in (400, 300):
                    chat.scroll_to(y=0, animate=False, immediate=True)
                    await _until(
                        pilot, lambda before=before: reads[-1].get('before') == before, what=f'the page before {before}'
                    )
                    await _until(pilot, lambda: chat.scroll_offset.y > 0, what='the restored reading position')
                assert len(chat._pages) == 3

                # The window still ends the conversation, so coming back down asks
                # for nothing.
                asked = len(reads)
                chat.scroll_end(animate=False)
                await pilot.pause()
                await pilot.pause()
                assert len(reads) == asked, 'a window that ends the conversation has nothing to read'

                # The page above arrives while the reader is at the bottom.
                page = await client.transcript(limit=DEFAULT_TRANSCRIPT_WINDOW, before=chat.older_cursor)
                await chat.prepend_items(page['items'], first_index=page['first_index'], has_more=page['has_more'])
                await pilot.pause()

                assert len(chat._pages) == 3, 'the window is still bounded'
                assert 'question 249' in _transcript(app), 'the page the reader is inside stays'
                assert 'question 050' not in _transcript(app), 'the page furthest from the reader is the one that left'
                assert chat.older_cursor == 200, 'the window starts where the page after it starts'
                assert chat.has_older is True, 'and the page that left can be asked for again'
                assert chat._reaches_end is True, 'nothing left from the bottom'

    asyncio.run(inner())


def test_a_page_dropped_from_the_top_leaves_no_hole_in_the_transcript(composition: Composition, tmp_path: Path) -> None:
    """The cursor walks in items, so the page that left is asked for exactly.

    The conversation is seeded with reasoning and with a tool call and its result, so a
    page paints fewer rows than the items it stands for. Once the reader is at the
    bottom, the page let go of is the one at the top; the cursor that replaces it must
    be where that page ended, not where its widgets would put it. A short cursor asks
    for a page that ends too early, and the items in between are never painted.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_uneven_session(runtime, 200)
            reads = _windows(client)
            app = _windowed(client, _session(runtime))
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat._pages) == 1, what='the newest window')

                for pages in (2, 3):
                    chat.scroll_to(y=0, animate=False, immediate=True)
                    await _until(pilot, lambda pages=pages: len(chat._pages) == pages, what=f'{pages} pages')
                    await _until(pilot, lambda: chat.scroll_offset.y > 0, what='the restored reading position')
                assert len(chat._pages) == 3, 'the window is at its bound'

                # A page above arrives while the reader is at the bottom, so the page
                # furthest from them — the one at the top — is the one let go of.
                chat.scroll_end(animate=False)
                await pilot.pause()
                cursor = chat.older_cursor
                page = await client.transcript(limit=DEFAULT_TRANSCRIPT_WINDOW, before=cursor)
                await chat.prepend_items(page['items'], first_index=page['first_index'], has_more=page['has_more'])
                await pilot.pause()

                span = cursor - page['first_index']
                assert span == len(page['items']), 'a page stands for exactly its span of the transcript'
                assert len(chat._pages) == 3, 'the window is still bounded'
                assert chat.older_cursor == page['first_index'] + span, (
                    'the cursor moved by the items the page stood for, not by the widgets it mounted'
                )

                # Asking for the page above must hand back exactly the items that left.
                asked = len(reads)
                chat.scroll_to(y=0, animate=False, immediate=True)
                await _until(pilot, lambda: len(reads) == asked + 1, what='the page above again')
                await _until(pilot, lambda: chat.scroll_offset.y > 0, what='the restored reading position')
                await pilot.pause()

                assert reads[-1] == {'limit': DEFAULT_TRANSCRIPT_WINDOW, 'before': cursor}
                painted = sorted(int(found) for found in re.findall(r'question (\d{3})', _transcript(app)))
                assert painted, 'the window paints the prompts'
                assert painted == list(range(painted[0], painted[-1] + 1)), (
                    f'the window has a hole between {painted[0]} and {painted[-1]}'
                )

    asyncio.run(inner())


def test_a_click_on_a_message_in_the_window_opens_its_rollback_point(composition: Composition, tmp_path: Path) -> None:
    """The line keeps the service's absolute position, not its place in the window.

    With a window of two items the clicked prompt is the second item of the page and
    the third message of the session: a window that renumbered its items would open
    the wrong turn, or none.
    """

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            await _seed_turn(runtime, client, 'first question')
            await _seed_turn(runtime, client, 'second question')
            points = (await client.rollback_points())['points']
            second = next(point for point in points if point['preview'] == 'second question')

            seen: list[Any] = []

            async def capture(screen: Any) -> None:
                seen.append(screen)

            app = _windowed(client, _session(runtime), window=2)
            app.push_screen_wait = capture
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: 'second question' in _transcript(app), what='the newest window')
                assert 'first question' not in _transcript(app), 'a window of two items is the window'

                painted = [widget for widget in chat.children if getattr(widget, 'seq', None) == second['seq']]
                assert painted, 'the line carries the absolute position the service reported'

                painted[0].on_click()
                await _until(pilot, lambda: bool(seen), what='the rollback list')
                assert seen[0]._preselected == second['turn_id'], 'the list starts on the clicked turn'

    asyncio.run(inner())


def test_a_transcript_with_nothing_above_asks_for_nothing(composition: Composition, tmp_path: Path) -> None:
    """`has_more` false is the end of the conversation, not an empty page to ask for."""

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, _transport, client):
            _grow_session(runtime, 120)
            reads = _windows(client)
            app = _windowed(client, _session(runtime), window=500)
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await _until(pilot, lambda: len(chat.children) == 240, what='the whole conversation in one page')
                asked = len(reads)

                chat.scroll_to(y=0, animate=False, immediate=True)
                await pilot.pause()
                await pilot.pause()

                assert chat.has_older is False
                assert len(reads) == asked, 'nothing older exists, so nothing is asked for'
                assert len(chat.children) == 240, 'and nothing is painted'

    asyncio.run(inner())


def test_the_transcript_window_preference_falls_back_and_clamps(tmp_path: Path) -> None:
    """The window is a preference, so an edited file cannot break the attach."""

    path = tmp_path / 'ui.json'
    store = PreferencesStore(path)
    assert store.load().transcript_window == DEFAULT_TRANSCRIPT_WINDOW, 'a missing file is the default'

    store.save(Preferences().with_transcript_window(40))
    assert store.load().transcript_window == 40, 'a usable window survives the round trip'

    path.write_text(json.dumps({'transcript_window': 'lots'}), encoding='utf-8')
    assert store.load().transcript_window == DEFAULT_TRANSCRIPT_WINDOW, 'a wrong type is not worth a failure'

    path.write_text(json.dumps({'transcript_window': 10_000}), encoding='utf-8')
    assert store.load().transcript_window == MAX_TRANSCRIPT_WINDOW, 'a window the service would refuse is pulled down'


def test_cut_stream_relaunches_service_and_reports_one_outage(composition: Composition, tmp_path: Path) -> None:
    from src.server.launch import ServiceDied

    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _attached):

            class Unavailable(httpx.AsyncBaseTransport):
                online = False
                cuts = 0

                async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                    if self.online:
                        return await transport.handle_async_request(request)
                    if request.url.path == '/events':
                        self.cuts += 1
                        return httpx.Response(200, content=b'')
                    raise httpx.ConnectError('Connection refused', request=request)

            outage = Unavailable()
            attempts = 0

            def launch(**kwargs: Any) -> Launch:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise ServiceDied('The candidate exited before publishing')
                outage.online = True
                return Launch(endpoint=SERVICE)

            client = ServiceClient(SERVICE, transport=outage, launch=launch)
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: attempts >= 3 and app._service_state is not None, what='relaunch')
                    text = _transcript(app)
                    assert text.count('No Sigil service answered') == 1
                    assert text.count('may be missing') <= 1
                    assert 'What you typed is back' not in text
                    assert outage.cuts >= 2, 'More than one failed retry is exercised'
                    await pilot.press('h', 'i', 'enter')
                    await _until(pilot, lambda: 'done' in _transcript(app), what='a working service after recovery')
            finally:
                await client.aclose()

    asyncio.run(inner())


def test_restart_on_same_port_refreshes_token_instead_of_stopping_following(
    composition: Composition, tmp_path: Path
) -> None:
    async def inner() -> None:
        async with _service_under_test(composition, tmp_path) as (runtime, transport, _attached):
            stale = Endpoint(host=SERVICE.host, port=SERVICE.port, pid=0, token='previous-run', started_at=0)
            attempts = 0

            def launch(**kwargs: Any) -> Launch:
                nonlocal attempts
                attempts += 1
                return Launch(endpoint=SERVICE)

            client = ServiceClient(stale, transport=transport, launch=launch)
            try:
                app = _terminal(client, _session(runtime))
                async with app.run_test(size=(100, 40)) as pilot:
                    await _until(pilot, lambda: app._service_state is not None, what='the new run token')
                    assert attempts == 1
                    assert app._service_following
                    await pilot.press('h', 'i', 'enter')
                    await _until(pilot, lambda: 'done' in _transcript(app), what='answer after token rotation')
            finally:
                await client.aclose()

    asyncio.run(inner())
