"""How the terminal talks to the local service: one client, no widgets.

The service is the only thing that executes a turn, so the terminal needs a way to
find it, ask it for the state of a session, send a message into the queue it owns,
stop a turn, answer a question and follow what happens. That is this module, and
nothing else: the mixins that decide what the screen looks like are the next cut
(`tui-attach-v1`), and they consume this surface instead of composing an engine.

Six decisions are worth writing down, because each one is a refusal of a simpler
design.

- **`httpx`, not `urllib` in a thread.** The terminal is one asyncio loop: an event
  stream, a render loop and a turn all share it. `urllib` and `http.client` are
  blocking, so every call would need `asyncio.to_thread`, and a stream — the one
  call that must stay open for minutes — cannot be moved to a thread at all without
  a queue in front of it. `httpx.AsyncClient` gives an awaitable request, an
  abortable read and a streaming body natively, and it is already behind the parity
  tests' ASGI transport. It is declared in `pyproject.toml` because production code
  now imports it, even though it arrived transitively before.
- **The transport is a constructor argument.** `httpx.ASGITransport` drives the real
  application in process, and a scripted transport hands over bytes a server would
  never produce on purpose — a stream cut in the middle of an event, a body that
  quotes the token. No test needs a socket or a child process to exercise this
  module.
- **The token is a header, never a word.** It is read from the endpoint file and put
  into the client's default headers once. It does not appear in a URL, in a log, in
  an error message or in `repr`, and the client scrubs it out of anything the
  service says before raising: a remote that echoes the credential must not become a
  leak in a terminal.
- **The failure vocabulary has names.** `ServiceUnavailable`, `Unauthorized`,
  `ServiceBusy`, `RequestRejected`, `PayloadTooLarge`, `UnexpectedResponse` and
  `StreamInterrupted`. A screen can act on the difference — retry, ask for the
  password, wait for the turn, fix the message — and none of them is a bare
  `httpx` error reaching the caller.
- **The SSE parser is a separate state machine.** `SseDecoder` reads bytes and
  returns frames; it knows nothing about HTTP. That is what makes the interesting
  cases cheap to test: an event split across two reads, three `data:` lines, a
  comment, CRLF, a character cut between its bytes, and a stream that stops before
  the blank line that would have ended an event.
- **One connection per stream, and the cursor belongs to the caller.** `events`
  yields parsed events and raises `StreamInterrupted` carrying the last id it read
  when the connection ends without being closed. Reconnecting is `events(after=…)`
  with that id, and the service replays what was missed. Reconnecting inside this
  client would need a delay loop of its own, which is a policy the screen should
  own rather than hide.

The client is a thin transport on purpose: it does not validate a message, decide
whether a session is busy, or hold a transcript. Those are the service's decisions,
and a client that repeated them would eventually disagree with it.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote

import httpx

from src.engine.storage.database import database_path
from src.server.endpoint import Endpoint
from src.server.launch import DEFAULT_READY_TIMEOUT, Launch, LaunchError, ensure_service

DEFAULT_TIMEOUT = 30.0
"""Seconds one request may take.

A turn is acknowledged when it is queued, not when it finishes, so no request this
client makes is as long as a turn. The event stream is the exception and passes
`timeout=None`: it is supposed to stay open, and the service's own heartbeat is
what says it is alive.
"""

REDACTED = '[redacted]'
"""What a message says instead of the token, when the service echoed it."""

STATUS_ERRORS: dict[int, type[ServiceClientError]]
"""The statuses this client can name, filled once the classes below exist."""


class ServiceClientError(Exception):
    """The base of every failure this client reports.

    ``code`` is a stable identifier so a caller can branch on the kind without
    parsing prose, the same contract `src/server/launch.py` keeps.
    """

    code = 'service_error'


class ServiceUnavailable(ServiceClientError):
    """No service answered, or none could be started.

    ``launch_code`` carries the launcher's own identifier when the failure came
    from `src/server/launch.py` — ``service_starting`` means "retry in a moment",
    ``service_died`` means "the start-up failed, read the message" — so the screen
    can say which of the two it is without matching text.
    """

    code = 'service_unavailable'

    def __init__(self, message: str, *, launch_code: str | None = None) -> None:
        super().__init__(message)
        self.launch_code = launch_code


class Unauthorized(ServiceClientError):
    """The run token was refused: this client is talking to another run."""

    code = 'unauthorized'


class ServiceBusy(ServiceClientError):
    """The request was well formed and cannot be applied while the session is busy."""

    code = 'busy'


class RequestRejected(ServiceClientError):
    """The request names something the service will not accept; a different one would."""

    code = 'rejected'


class PayloadTooLarge(ServiceClientError):
    """The body passed the service's command ceiling and was refused unread."""

    code = 'too_large'


class NoHistoryYet(ServiceClientError):
    """The service has no durable row for this session yet: nothing has been said.

    A conversation that was never saved has no transcript to paint, and that is a
    normal state, not a failure. The screen shows an empty transcript and keeps
    listening; a sentence about an unreadable reply would be a lie about a service
    that answered a correct 404.
    """

    code = 'no_history_yet'


class UnexpectedResponse(ServiceClientError):
    """The reply is not the shape this client knows: not JSON, or a status it cannot name."""

    code = 'unexpected_response'


class StreamInterrupted(ServiceClientError):
    """The event stream ended without the client closing it.

    ``last_event_id`` is the cursor to reconnect from: passing it to
    `ServiceClient.events` asks the service for what was missed, which is the whole
    reason the stream carries sequence numbers.
    """

    code = 'stream_interrupted'

    def __init__(self, message: str, *, last_event_id: int | None) -> None:
        super().__init__(message)
        self.last_event_id = last_event_id


STATUS_ERRORS = {
    400: RequestRejected,
    401: Unauthorized,
    409: ServiceBusy,
    413: PayloadTooLarge,
}


@dataclass(frozen=True, slots=True)
class OutgoingImage:
    """One picture of a message, in the order the text marks it.

    ``media_type`` is what the client claims the bytes are and ``data`` is their
    base64 payload, exactly the shape `/api/turn` accepts. A position is not a
    field: the list's order is the ``[Image #n]`` number, which is why
    `TurnRequest` keeps a tuple.
    """

    media_type: str
    data: str


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """One message to send, with the identity a retry has to reuse.

    The id is generated here, on the client, because the queue's deduplication is
    keyed by it: a retry that let the service invent a second id would be a second
    message, which is the failure the durable queue exists to prevent. Retrying the
    same instance is therefore the correct retry, and a new instance is a new
    message.
    """

    text: str
    images: tuple[OutgoingImage, ...] = ()
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def payload(self) -> dict[str, Any]:
        """The body `/api/turn` takes, with the images in their order."""
        return {
            'text': self.text,
            'request_id': self.request_id,
            'images': [{'media_type': image.media_type, 'data': image.data} for image in self.images],
        }


@dataclass(frozen=True, slots=True)
class SseFrame:
    """One event as the SSE protocol defines it, before any JSON is read.

    ``data`` is the joined `data:` lines and nothing is interpreted: the protocol
    is a text format and this type says so.
    """

    id: str | None = None
    event: str | None = None
    data: str = ''


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    """One event the service published, ready to apply to the screen.

    ``id`` is the service's sequence number, which is the cursor of a reconnect. It
    is ``None`` when the frame carried no usable `id`, and a frame without one does
    not advance the cursor.
    """

    id: int | None
    type: str
    data: dict[str, Any]


class SseDecoder:
    """Reads server-sent events one chunk at a time.

    A chunk boundary is not an event boundary: a read can end in the middle of a
    field, of a line, of an event, or of a multi-byte character, and a decoder that
    assumed otherwise would lose or mangle an event exactly when the network was
    busy. So bytes are accumulated, whole lines are consumed, and a frame is
    returned only when its blank line arrives. An event cut before that line is
    held and never emitted: an incomplete frame is a broken event, not a short one.

    The parser follows the protocol for the fields this service sends (`id`,
    `event`, `data`) and ignores the rest, including comment lines — the service's
    `: keep-alive` heartbeat is a comment, and it must not become an event. The
    last id is deliberately *not* carried into a frame that has none: the cursor
    here is per event, and inventing one would make a reconnect skip an event.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self._buffer = ''
        self._id: str | None = None
        self._event: str | None = None
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> list[SseFrame]:
        """Every frame the chunk completed, in order. Possibly none."""
        self._buffer += self._decoder.decode(chunk)
        frames: list[SseFrame] = []
        while (line := self._take_line()) is not None:
            frame = self._consume(line)
            if frame is not None:
                frames.append(frame)
        return frames

    def _take_line(self) -> str | None:
        """The next whole line, or ``None`` while the rest is still incomplete.

        A trailing ``\\r`` may be the first half of ``\\r\\n``, so it waits for the
        next read instead of being read as a line and an empty line.
        """
        for index, character in enumerate(self._buffer):
            if character == '\n':
                line, self._buffer = self._buffer[:index], self._buffer[index + 1 :]
                return line.removesuffix('\r')
            if character == '\r':
                if index + 1 == len(self._buffer):
                    return None
                step = 2 if self._buffer[index + 1] == '\n' else 1
                line, self._buffer = self._buffer[:index], self._buffer[index + step :]
                return line
        return None

    def _consume(self, line: str) -> SseFrame | None:
        if not line:
            return self._dispatch()
        if line.startswith(':'):
            return None
        name, _, value = line.partition(':')
        value = value.removeprefix(' ')
        if name == 'id':
            self._id = value
        elif name == 'event':
            self._event = value
        elif name == 'data':
            self._data.append(value)
        return None

    def _dispatch(self) -> SseFrame | None:
        if not self._data:
            self._id = self._event = None
            return None
        frame = SseFrame(id=self._id, event=self._event, data='\n'.join(self._data))
        self._id = self._event = None
        self._data = []
        return frame


async def _start_service(
    launch: Callable[..., Launch], *, database: Path, endpoint_path: Path | None, wait: float
) -> Launch:
    """Runs the launcher off the loop and names the failure it reports.

    The launcher may spawn a process and watch for twenty seconds, so it cannot run
    on the loop the terminal draws from. Both callers — the first attach and a
    recovery — need exactly these lines, and a second copy would be a second
    vocabulary for the same error.
    """
    try:
        return await asyncio.to_thread(launch, database=database, endpoint_path=endpoint_path, wait=wait)
    except LaunchError as exc:
        raise ServiceUnavailable(f'The Sigil service is not available: {exc}', launch_code=exc.code) from exc


class ServiceClient:
    """The terminal's side of the local service.

    One instance per attached service, closed with `aclose` when the terminal
    leaves. Every method is one request, maps the reply to a typed value or a typed
    failure, and does nothing else.
    """

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        launch: Callable[..., Launch] = ensure_service,
        database: str | Path | None = None,
        endpoint_path: Path | None = None,
        wait: float = DEFAULT_READY_TIMEOUT,
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        # How to find a service again after one restarted on another port. It lives
        # here because the address is fixed at construction and this is the only
        # object that can re-aim itself without losing the transport it was given.
        self._launch = launch
        self._database = database_path() if database is None else Path(database)
        self._endpoint_path = endpoint_path
        self._wait = wait
        self._aim(endpoint)
        self._client = self._build()

    def _aim(self, endpoint: Endpoint) -> None:
        """Takes the address and the credential from ``endpoint``."""
        self._host = endpoint.host
        self._port = endpoint.port
        self._token = endpoint.token

    def _build(self) -> httpx.AsyncClient:
        """A connection pool for the current address, over the transport this client holds.

        The transport goes on every build, not only the first: a rebuilt pool without
        it would dial the network from a test that handed the client a double, which is
        both a false result and a hang.
        """
        return httpx.AsyncClient(
            base_url=f'http://{self._host}:{self._port}',
            headers={'authorization': f'Bearer {self._token}'},
            transport=self._transport,
            timeout=self._timeout,
        )

    def __repr__(self) -> str:
        """The address and nothing else: a client gets printed in logs and reports."""
        return f'ServiceClient(host={self._host!r}, port={self._port!r})'

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Releases the connection pool. Idempotent, and the only clean shutdown."""
        await self._client.aclose()

    async def _rebuild(self, endpoint: Endpoint) -> None:
        """Aims this client at ``endpoint``: one object, new address, new pool.

        The replacement is built before the old pool is closed, so a failure here
        leaves a working client instead of a dead one. Closing the old pool is what
        makes recovery repeatable: without it every restart would leak a pool.
        """
        previous = self._client
        self._aim(endpoint)
        self._client = self._build()
        await previous.aclose()

    async def refresh_endpoint(self) -> bool:
        """Re-discovers the service and re-aims this client at whichever answers now.

        A service that restarted listens on another port and publishes another token,
        and this client fixes both at construction — which is right for every other
        request, because an address that changed without being noticed would be a
        client that guessed. Recovering from a restart is therefore explicit, and it
        is this method's whole job: discover, compare, rebuild in place.

        Returns:
            Whether the endpoint changed. ``False`` means the service that answered
            is the one this client already talks to, so nothing was rebuilt and the
            caller's event cursor is still valid.

        Raises:
            ServiceUnavailable: the launcher gave up, or nothing answered. A caller
                with a retry loop keeps retrying; there is nothing else to do here.
        """
        started = await _start_service(
            self._launch, database=self._database, endpoint_path=self._endpoint_path, wait=self._wait
        )
        endpoint = started.endpoint
        if (endpoint.host, endpoint.port, endpoint.token) == (self._host, self._port, self._token):
            return False
        await self._rebuild(endpoint)
        return True

    @classmethod
    async def connect(
        cls,
        *,
        database: str | Path | None = None,
        endpoint_path: Path | None = None,
        launch: Callable[..., Launch] = ensure_service,
        transport: httpx.AsyncBaseTransport | None = None,
        wait: float = DEFAULT_READY_TIMEOUT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> ServiceClient:
        """Attaches to a live service, starting one only when there is none.

        `ensure_service` is the only arbiter of who may start a server, and it is
        blocking by design: it may spawn a process and watch for twenty seconds. It
        is therefore run off the loop, because the terminal draws from that loop and
        a frozen screen for twenty seconds is worse than a slow one.

        Args:
            database: The database whose ownership lock arbitrates. Defaults to the
                one this data directory uses, which is what the child resolves too.
            endpoint_path: The rendezvous file. Defaults to the one this data
                directory publishes.
            launch: How to obtain a service. `ensure_service`; a test passes a
                callable returning a `Launch` so no process is started.
            transport: The HTTP transport. `None` dials the service; a test passes
                `httpx.ASGITransport` or a scripted double.
            wait: Seconds allowed for a publication after the decision.
            timeout: Seconds one request may take.

        Raises:
            ServiceUnavailable: the launcher gave up, or nothing answered. Its
                ``launch_code`` names which failure the launcher reported.
        """
        target = database_path() if database is None else Path(database)
        started = await _start_service(launch, database=target, endpoint_path=endpoint_path, wait=wait)
        return cls(
            started.endpoint,
            transport=transport,
            timeout=timeout,
            launch=launch,
            database=target,
            endpoint_path=endpoint_path,
            wait=wait,
        )

    # --- reading ------------------------------------------------------------

    async def state(self) -> dict[str, Any]:
        """Everything the screen paints from: session, workspace, model, tree, question."""
        return await self._get('/api/state')

    async def context(self) -> dict[str, Any]:
        """The meter's projection: how full the window is, what it costs.

        The numbers are the service's own measurement of the session it owns. A
        client that measured its own copy would be a second opinion about one
        conversation, and the one on screen would be the wrong one.
        """
        return (await self._get('/api/context')).get('context') or {}

    async def external_files(self, allow: bool) -> dict[str, Any]:
        """Allows or denies outside-workspace file access for this session.

        A session in the middle of a turn is refused by the service (``409``), which
        arrives as `ServiceBusy` carrying the service's own reason.
        """
        return (await self._post('/api/external-files', {'allow': allow})).get('session') or {}

    async def mcp_servers(self) -> list[dict[str, Any]]:
        """Every configured MCP server, as the service projects it."""
        return list((await self._get('/api/mcp')).get('servers') or ())

    async def toggle_mcp(self, name: str, enabled: bool) -> list[dict[str, Any]]:
        """Enables or disables one server for this session, returning the new list.

        Refused mid-turn or while a subagent runs: both are ``409``, and the reason
        is the service's, not one this client guessed.
        """
        payload = await self._post('/api/mcp', {'name': name, 'enabled': enabled})
        return list((payload.get('mcp') or {}).get('servers') or ())

    async def effort(self, level: str) -> dict[str, Any]:
        """Sets the reasoning level and returns the model projection it produced."""
        return (await self._post('/api/effort', {'effort': level})).get('model') or {}

    async def select_agent(self, name: str) -> dict[str, Any]:
        """Activates a profile for the next turn, answered by the service.

        Refused while a turn runs (``409``) and for a name nothing declares
        (``400``); both arrive as the client's typed failures carrying the
        service's own reason.
        """
        return await self._post('/api/agent', {'name': name})

    async def new_session(self, worktree: str | None = None) -> dict[str, Any]:
        """Starts an empty conversation, in a worktree cut first when one is named."""
        body = {} if not worktree else {'worktree': worktree}
        return (await self._post('/api/session/new', body)).get('session') or {}

    async def create_worktree(self, name: str) -> dict[str, Any]:
        """Cuts a worktree and moves the service into it."""
        return await self._post('/api/worktree', {'name': name})

    async def sessions(self) -> dict[str, Any]:
        """Every conversation of this repository, newest first, plus the current id."""
        return await self._get('/api/sessions')

    async def open_session(self, session_id: str) -> dict[str, Any]:
        """Opens an existing conversation, wherever its workspace is."""
        return (await self._post('/api/session/open', {'id': session_id})).get('session') or {}

    async def models(self) -> dict[str, Any]:
        """The model in effect and the accounts a client may offer."""
        return await self._get('/api/models')

    async def catalog_for(self, alias: str) -> dict[str, Any]:
        """One account's model list, as the service projected it.

        The route bounds its own wait, so a slow provider arrives here as a typed
        refusal with a sentence rather than as a request that never returns.
        """
        return await self._get(f'/api/models/{alias}')

    async def set_model(self, account: str, model: str, thinking: str | None = None) -> dict[str, Any]:
        """Switches the model of the session, refused while a turn runs."""
        body: dict[str, Any] = {'account': account, 'model': model}
        if thinking is not None:
            body['thinking'] = thinking
        return await self._post('/api/model', body)

    async def model_defaults(self) -> dict[str, Any]:
        """Both config files that decide what a profile starts on, per file.

        Never refused, mid-turn included: the screen shows the state before the user
        edits it, and reading the files changes nothing. The projection carries the
        files, the targets with the entry each one resolves to, and the accounts those
        entries name — so a client paints the defaults without holding a copy of the
        precedence rule that decides them.
        """
        return (await self._get('/api/model-defaults')).get('defaults') or {}

    async def write_model_default(
        self, target: str | None, scope: str, config: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Sets or clears one default, in the file ``scope`` names.

        ``target`` is an agent name, or ``None`` for the block a conversation starts
        on; ``config`` is ``None`` to clear the entry, and saying so is required
        because clearing has to be spelled out. A turn in the way is a ``409``
        carrying the engine's own sentence, and it arrives here as `ServiceBusy`: the
        write is the service's decision, so this method reports it instead of
        second-guessing it.

        Returns:
            The projection the write left behind, in the same shape
            `model_defaults` answers with.
        """
        body: dict[str, Any] = {
            'target': target,
            'scope': scope,
            'config': None if config is None else dict(config),
        }
        return (await self._post('/api/model-defaults', body)).get('defaults') or {}

    async def rollback_points(self) -> dict[str, Any]:
        """The turns the open session can return to, newest first."""
        return await self._get('/api/rollback')

    async def rollback(self, turn_id: str, *, files: bool = True) -> dict[str, Any]:
        """Returns the session to before a turn, and reports what it dropped."""
        return await self._post('/api/rollback', {'turn_id': turn_id, 'files': files})

    async def fork(self, turn_id: str) -> dict[str, Any]:
        """Branches a new session at the instant before a turn."""
        return await self._post('/api/fork', {'turn_id': turn_id})

    async def accounts(self) -> dict[str, Any]:
        """The provider accounts, with whether each holds a credential."""
        return await self._get('/api/accounts')

    async def add_account(
        self, alias: str, provider: str, api_key: str = '', *, auth: str = 'api_key'
    ) -> dict[str, Any]:
        """Adds an account and makes it active, refused while a turn runs."""
        return await self._post(
            '/api/accounts', {'alias': alias, 'provider': provider, 'api_key': api_key, 'auth': auth}
        )

    async def activate_account(self, alias: str) -> dict[str, Any]:
        """Makes an existing account active."""
        return await self._post(f'/api/accounts/{alias}/activate', {})

    async def delete_account(self, alias: str) -> dict[str, Any]:
        """Removes an account and its credential."""
        return await self._delete(f'/api/accounts/{alias}')

    async def rename_account(self, alias: str, new_alias: str) -> dict[str, Any]:
        """Changes an account's label."""
        return await self._post(f'/api/accounts/{alias}/rename', {'new_alias': new_alias})

    async def transcript(self, *, limit: int | None = None, before: int | None = None) -> dict[str, Any]:
        """One window of the conversation, newest last.

        ``limit`` is how many items to return and ``before`` is the position the page
        ends just before; leaving it out asks for the newest page, which is the only
        one that also carries ``pending`` — the rows waiting in the queue. A terminal
        needs both halves to attach, and one windowed read is enough: asking for the
        whole transcript to learn of a queued message is the cost this route removes.

        The reply keeps every item's absolute ``index``, so a click still maps to its
        rollback point, and carries ``first_index`` and ``has_more`` for walking the
        cursor back.
        """
        query: list[str] = []
        if limit is not None:
            query.append(f'limit={int(limit)}')
        if before is not None:
            query.append(f'before={int(before)}')
        path = '/api/session/transcript'
        return await self._get(f'{path}?{"&".join(query)}' if query else path)

    async def durable(self) -> dict[str, Any]:
        """The durable session: transcript, queue, deliveries and recovery facts.

        A session with no row yet answers 404, which this method reports as
        `NoHistoryYet`: an empty conversation, not a refusal. Every other status keeps
        its usual meaning.
        """
        return await self._get('/api/session/durable', absent=NoHistoryYet)

    # --- acting -------------------------------------------------------------

    async def send_turn(self, request: TurnRequest) -> dict[str, Any]:
        """Accepts a message durably: a new turn, or a steer into the running one.

        The reply carries ``request_id`` — the one this client generated — and the
        identifiers of the stored images. Whether the text is acceptable is the
        service's decision: an empty message is a `RequestRejected`, not something
        this method second-guesses.
        """
        return await self._post('/api/turn', request.payload())

    async def abort(self) -> bool:
        """Stops the running turn. ``False`` when there was nothing to stop."""
        return bool((await self._post('/api/abort', {})).get('stopped'))

    # --- the tree ------------------------------------------------------------

    async def tree_node(self, node_id: str, *, include_messages: bool = False) -> dict[str, Any]:
        """One node of the session's tree, addressed by id or a unique prefix.

        ``include_messages`` asks for the transcript the subagent view paints. It is
        advisory: the route always returns the transcript, so this flag documents the
        caller's intent and shapes the request, never what comes back. The node is
        the service's, and a ref nothing names is a ``400`` — `RequestRejected`
        carrying the service's own sentence.
        """
        path = f'/api/tree/node/{quote(node_id, safe="")}'
        if include_messages:
            path = f'{path}?include_messages=true'
        return (await self._get(path)).get('node') or {}

    async def stop_node(self, node_id: str) -> dict[str, Any]:
        """Stops a subagent and everything it summoned, in the service's tree.

        The reply carries the node, the tree it lives in and the engine's sentence.
        The root is refused with ``409`` — `ServiceBusy` carrying its reason, not a
        sentence this client invented.
        """
        return await self._post('/api/tree/stop', {'node_id': node_id})

    async def background_node(self, node_id: str) -> dict[str, Any]:
        """Sends a running subagent to the background: it answers as a message later.

        A node that already finished, and the session's own agent, are ``409`` for
        the engine's own reasons; ``400`` names a node the tree does not have.
        """
        return await self._post('/api/tree/background', {'node_id': node_id})

    async def answer(self, question_id: str, answers: Sequence[Mapping[str, Any]] | None = None) -> bool:
        """Answers the question the service is waiting on. ``None`` cancels it.

        ``False`` means the question is no longer pending — answered elsewhere, or
        withdrawn because the turn ended — and the screen should re-read the state
        rather than retry.
        """
        body = {'id': question_id, 'answers': None if answers is None else [dict(item) for item in answers]}
        return bool((await self._post('/api/answer', body)).get('resolved'))

    # --- following ----------------------------------------------------------

    async def events(self, *, after: int = 0) -> AsyncIterator[ServiceEvent]:
        """The event stream from ``after``, on one connection.

        `after` is the last sequence number the caller applied; the service replays
        what came later and then continues live. The cursor goes out as both
        `Last-Event-ID` and `?after=` because the two reconnection styles differ —
        a browser sends the header by itself, a hand-rolled client passes the query
        — and the service reads either.

        The generator ends one of two ways. Closed by the caller, which is not an
        error and releases the connection. Or broken by the service, which raises
        `StreamInterrupted` carrying the last id read, so the caller reconnects with
        `events(after=that id)` and misses nothing.
        """
        cursor = max(0, after)
        request = self._client.build_request(
            'GET',
            '/events',
            params=None if cursor == 0 else {'after': str(cursor)},
            headers={} if cursor == 0 else {'last-event-id': str(cursor)},
            timeout=None,
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(self._redacted(f'No Sigil service answered at {self._where()}: {exc}')) from exc
        try:
            if response.status_code >= 400:
                await response.aread()
                self._raise_for_status(response)
            decoder = SseDecoder()
            async for chunk in response.aiter_bytes():
                for frame in decoder.feed(chunk):
                    event = self._event(frame)
                    if event.id is not None:
                        cursor = event.id
                    yield event
            raise StreamInterrupted(
                self._redacted('The event stream closed before the client asked it to.'),
                last_event_id=cursor,
            )
        except httpx.HTTPError as exc:
            raise StreamInterrupted(self._redacted(f'The event stream broke: {exc}'), last_event_id=cursor) from exc
        finally:
            await response.aclose()

    # --- the wire -----------------------------------------------------------

    async def _get(self, path: str, *, absent: type[ServiceClientError] | None = None) -> dict[str, Any]:
        return await self._send(self._client.build_request('GET', path), absent=absent)

    async def _post(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return await self._send(self._client.build_request('POST', path, json=body))

    async def _delete(self, path: str) -> dict[str, Any]:
        return await self._send(self._client.build_request('DELETE', path))

    async def _send(self, request: httpx.Request, *, absent: type[ServiceClientError] | None = None) -> dict[str, Any]:
        """One request, its refusals named, its body read as a JSON object.

        ``absent`` names the failure for a ``404``. "It is not there yet" is a
        different sentence from "the service refused this", and only the caller knows
        which resource it asked about — so the caller names the error and the wire
        mapping stays here.
        """
        try:
            response = await self._client.send(request)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(self._redacted(f'No Sigil service answered at {self._where()}: {exc}')) from exc
        self._raise_for_status(response, absent=absent)
        return self._payload(response)

    def _raise_for_status(self, response: httpx.Response, *, absent: type[ServiceClientError] | None = None) -> None:
        """Raises the named failure for a status this client understands."""
        if response.status_code < 400:
            return
        if response.status_code == 404 and absent is not None:
            raise absent(self._redacted(_error_detail(response) or 'The service has no record of that yet.'))
        kind = STATUS_ERRORS.get(response.status_code, UnexpectedResponse)
        detail = _error_detail(response)
        message = detail if detail is not None else f'The service answered {response.status_code}.'
        raise kind(self._redacted(message))

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise UnexpectedResponse(
                self._redacted(f'The service answered {response.status_code} with a body that is not JSON.')
            ) from exc
        if not isinstance(payload, dict):
            raise UnexpectedResponse(self._redacted('The service answered with JSON that is not an object.'))
        return payload

    def _event(self, frame: SseFrame) -> ServiceEvent:
        try:
            payload = json.loads(frame.data)
        except ValueError as exc:
            raise UnexpectedResponse(self._redacted('The event stream sent data that is not JSON.')) from exc
        if not isinstance(payload, dict):
            raise UnexpectedResponse(self._redacted('The event stream sent an event that is not an object.'))
        return ServiceEvent(id=_cursor(frame.id), type=frame.event or 'message', data=payload)

    def _where(self) -> str:
        """The address a failure is told about. Never the credential."""
        return f'{self._host}:{self._port}'

    def _redacted(self, message: str) -> str:
        """The message with this run's token taken out.

        The service does not echo the token today. This is here so that the day it
        does — a debug route, a proxy, a mistake in an error body — the terminal's
        log gets a redacted line instead of a live credential.
        """
        return message.replace(self._token, REDACTED) if self._token else message


def _error_detail(response: httpx.Response) -> str | None:
    """The message the service put in its error body, when it put one there.

    Every refusal the routes produce is ``{"error": "..."}``; anything else is read
    as absent so the caller still gets a typed failure with the status in it.
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get('error'), str):
        return str(payload['error'])
    return None


def _cursor(raw: str | None) -> int | None:
    """A sequence number, or ``None`` when the frame's id is not one."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


__all__ = [
    'DEFAULT_TIMEOUT',
    'REDACTED',
    'NoHistoryYet',
    'OutgoingImage',
    'PayloadTooLarge',
    'RequestRejected',
    'ServiceBusy',
    'ServiceClient',
    'ServiceClientError',
    'ServiceEvent',
    'ServiceUnavailable',
    'SseDecoder',
    'SseFrame',
    'StreamInterrupted',
    'TurnRequest',
    'Unauthorized',
    'UnexpectedResponse',
]
