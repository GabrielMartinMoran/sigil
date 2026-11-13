"""The HTTP surface: JSON for state and actions, SSE for everything that moves.

Two decisions shape this file:

- **The browser is a view, not a client of the engine.** A route reads a projection
  (`views.py`) or issues one command against the runtime. Nothing here decides how
  anything looks, and nothing here reaches into the engine's objects to patch them.
- **State is fetched, events are applied.** `/api/state` answers the whole screen in
  one request, and `/events` carries only what changed with a sequence number. A
  client that reconnects sends `Last-Event-ID` and gets the events it missed, or asks
  for the state again when it lagged — which is why a delta is never the only copy of
  a fact.

The one exception is the token stream: `text_delta` is the typing, and re-reading a
transcript to redraw it would be worse than applying deltas that end with a
`turn_finished` the client uses to refetch.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.engine.agents.model_config import AgentModelConfig
from src.engine.agents.model_defaults import ConfigScope
from src.engine.agents.profile.models.agent_name import is_valid_agent_name
from src.engine.agents.profile.models.agent_not_found import AgentNotFound
from src.engine.agents.rollback import RollbackError, prompt_of_turn
from src.engine.agents.tree.models.summon_error import SummonError
from src.engine.context.meter.cost import session_cost
from src.engine.providers.domain.auth_method import AuthMethod, auth_methods_for
from src.engine.providers.domain.provider_error import BusyError, ProviderError
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.thinking import EFFORT_LEVELS, parse_choice
from src.engine.sessions.catalog import catalog
from src.engine.sessions.catalog import workspaces as discover_workspaces
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.engine.workspace.repository import GitRepository
from src.server import views
from src.server.auth import Auth, AuthMiddleware, LoginThrottle, cleared_cookie, client_key, session_cookie
from src.server.config import ServerConfig
from src.server.endpoint import EndpointPublisher
from src.server.events import EventHub
from src.server.runtime import DEFAULT_IDLE_GRACE, IDLE_POLL_INTERVAL, IncomingImage, ServerRuntime
from src.server.webbuild import dist_root, staleness

HEARTBEAT_SECONDS = 20.0
"""How often an idle stream says something, so a proxy does not close it."""

ACCOUNT_TIMEOUT_SECONDS = 30.0
"""Longest a route waits for a credential check.

Adding an account is the one call that reaches the provider with the user's key,
so it is the one that can hang. The bound is here for the same reason as the
catalogue's: a refusal with a sentence beats a request that never returns.
"""

OAUTH_OVER_HTTP = (
    'Browser sign-in is only available from the terminal running the engine, not over '
    'the API. Add this account there, or add it here with an API key.'
)
"""What an OAuth account is told. The flow needs a browser and the process that owns it."""

CATALOG_TIMEOUT_SECONDS = 15.0
"""Longest a client waits for one account's model list.

A provider that is slow is normal; one that never answers is not something a route
can hand back. The bound lives here and not only in the transport so the route cannot
hang a caller: a provider that never answers becomes a refusal with a sentence.
"""

MAX_COMMAND_BYTES = 64 * 1024 * 1024
"""Ceiling for one JSON command body, counted as the bytes arrive.

Ten images at the per-image limit are about 53 MiB once base64 adds a third; the
rest is the text and the envelope. A request over this is refused before it is
parsed, so an accidental upload cannot become a memory problem first.
"""

MAX_TRANSCRIPT_PAGE = 1000
"""Ceiling for one transcript window.

A window exists so a long conversation does not arrive in one body. A ceiling far
above any screen keeps that purpose: without one, ``limit`` would be a way to ask
for everything again, and the route would be as expensive as it was before it
paged.
"""

BACKEND_NOTICE = 'This is the Sigil backend; run `sigil serve` for the web client.'
"""What `GET /` answers when the bundle is not part of this surface.

A bare 404 is what a browser shows when a URL is wrong; this URL is right, and the
sentence names the command that serves the page. The backend is not a broken web
server — it is a different surface.
"""


class _BodyTooLarge(BaseException):
    """The body of the request passed `MAX_COMMAND_BYTES` while it was being read.

    It inherits ``BaseException`` on purpose. FastAPI reads a JSON body inside an
    ``except Exception`` and turns anything raised there into ``HTTPException(400)``,
    so a regular exception raised from ``receive`` would never reach this
    middleware and the client would see a parse error instead of the ``413``. This
    is deliberate control flow between the wrapped app and the middleware, not a
    program error, and it must not be caught by code that handles ordinary
    failures.
    """


class BodyLimitMiddleware:
    """Refuses an HTTP request whose body exceeds the command ceiling.

    A declared ``Content-Length`` is checked first, which is the common case and
    costs nothing. A chunked body has no length to check, so the bytes are counted
    as they arrive and the request is refused at the ceiling instead of after it.
    """

    def __init__(self, app: ASGIApp, *, limit: int = MAX_COMMAND_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.limit:
            await _too_large(send, self.limit)
            return
        counted = 0
        started = False

        async def limited() -> Message:
            nonlocal counted
            message = await receive()
            if message['type'] == 'http.request':
                counted += len(message.get('body', b''))
                if counted > self.limit:
                    raise _BodyTooLarge
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            await self.app(scope, limited, watched)
        except _BodyTooLarge:
            if started:  # the response is already going out; nothing left to refuse
                raise
            await _too_large(send, self.limit)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get('headers', ()):
        if name.lower() == b'content-length':
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(send: Send, limit: int) -> None:
    body = json.dumps({'error': f'The request body is larger than {_size(limit)}.'}).encode('utf-8')
    await send(
        {
            'type': 'http.response.start',
            'status': 413,
            'headers': [
                (b'content-type', b'application/json; charset=utf-8'),
                (b'content-length', str(len(body)).encode()),
            ],
        }
    )
    await send({'type': 'http.response.body', 'body': body})


def _size(limit: int) -> str:
    """The ceiling as a person reads it: MiB when it is whole, bytes otherwise.

    The second branch exists because the limit is injectable — a test drives it with
    a few hundred bytes, and ``0 MiB`` would be a message that says nothing.
    """
    if limit >= 1024 * 1024 and limit % (1024 * 1024) == 0:
        return f'{limit // (1024 * 1024)} MiB'
    return f'{limit} bytes'


def _conflict(exc: Exception) -> JSONResponse:
    """A command refused because the session is in the way, not the request.

    A running turn, a turn that is not a point of this session: the request was
    well formed and cannot be applied *now*, which is what ``409`` says. Repeating
    it after the turn ends is the expected next step.
    """
    return JSONResponse({'error': str(exc)}, status_code=409)


def _refused(exc: Exception) -> JSONResponse:
    """A command refused because the request itself names something unusable.

    An unknown account or profile, a level the model does not accept: a different
    request would work, so the answer is ``400`` and not ``409``.
    """
    return JSONResponse({'error': str(exc)}, status_code=400)


def _page_limit(raw: str | None) -> int | None:
    """``limit`` as a positive count, or "no window" when it was not asked for.

    Read as text and parsed here rather than declared as an ``int`` on the route:
    FastAPI answers a type error with its own ``422`` body, and a client that reads
    ``error`` would get nothing it can show. A sentence in the same vocabulary as
    every other refusal is what a caller can act on.
    """
    if raw is None:
        return None
    try:
        limit = int(raw)
    except ValueError:
        raise ValueError(f'limit must be a whole number of items, not {raw!r}.') from None
    if limit < 1:
        raise ValueError(f'limit must be at least 1, not {limit}.')
    if limit > MAX_TRANSCRIPT_PAGE:
        raise ValueError(f'limit must be at most {MAX_TRANSCRIPT_PAGE} items, not {limit}.')
    return limit


def _page_before(raw: str | None) -> int | None:
    """``before`` as a non-negative position, or "the newest page" when absent."""
    if raw is None:
        return None
    try:
        before = int(raw)
    except ValueError:
        raise ValueError(f'before must be a whole number, not {raw!r}.') from None
    if before < 0:
        raise ValueError(f'before must not be negative, not {before}.')
    return before


class LoginBody(BaseModel):
    """The only thing the login form sends."""

    password: str = Field(min_length=1)


class ImageBody(BaseModel):
    """One image of a message, in the order the text marks it.

    The shape is deliberately the bytes and nothing else: ``media_type`` is what the
    picture claims to be and ``data`` is its base64 payload. There is no filename
    field, because a client that sent a path would be asking the server to open a
    file, and the API never does that.
    """

    media_type: str = Field(min_length=1)
    data: str = Field(min_length=1)


class TurnBody(BaseModel):
    """A message from the composer: text, an optional request id, ordered images.

    ``images[0]`` is ``[Image #1]`` in the text; the position is the number, and it
    is part of what a retry has to match to be treated as the same message.
    """

    text: str = Field(min_length=1)
    request_id: str | None = None
    """Client-generated UUIDv4, reused by a retry of the same deliberate send."""
    images: list[ImageBody] = Field(default_factory=list)
    """Pictures in the order the text refers to them; bounded by the runtime."""


class AnswerBody(BaseModel):
    """An answer to the questions the agent asked."""

    id: str
    answers: list[dict[str, Any]] | None = None
    """``None`` cancels: the tool reports that the user declined."""


class WorkspaceBody(BaseModel):
    """A directory to move the server to."""

    root: str = Field(min_length=1)


class SessionBody(BaseModel):
    """A conversation to open."""

    id: str = Field(min_length=1)


class ModelBody(BaseModel):
    """A model to switch the session to.

    ``thinking`` is optional: omitting it keeps the level the model would pick by
    default, which is what the terminal's picker does when it only chooses a model.
    """

    account: str = Field(min_length=1)
    model: str = Field(min_length=1)
    thinking: str | None = None


class EffortBody(BaseModel):
    """The reasoning level to apply to the model in effect."""

    effort: str = Field(min_length=1)


class AccountBody(BaseModel):
    """A provider account to add, with the credential it needs.

    ``auth`` is accepted so an OAuth request is answered explicitly instead of
    being misread as a key-less API-key account.
    """

    alias: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    api_key: str | None = None
    auth: str = 'api_key'


class NewSessionBody(BaseModel):
    """A conversation to start, optionally in a worktree cut first."""

    worktree: str | None = None


class WorktreeBody(BaseModel):
    """A worktree to create and move into."""

    name: str = Field(min_length=1)


class RenameBody(BaseModel):
    """The new label for an account. The id, and saved sessions, stay valid."""

    new_alias: str = Field(min_length=1)


class AgentBody(BaseModel):
    """A profile to activate for the next turn."""

    name: str = Field(min_length=1)


class RollbackBody(BaseModel):
    """A rollback point to return to.

    ``files`` restores the workspace snapshot too; ``False`` puts the conversation
    back and leaves the tree as it is, which is the other choice the picker offers.
    """

    turn_id: str = Field(min_length=1)
    files: bool = True


class ForkBody(BaseModel):
    """A fork point: the instant before this turn becomes a new session."""

    turn_id: str = Field(min_length=1)


class NodeBody(BaseModel):
    """A node of the session tree, by id or a unique prefix of it."""

    node_id: str = Field(min_length=1)


class McpBody(BaseModel):
    """One MCP server to enable or disable for this session."""

    name: str = Field(min_length=1)
    enabled: bool


class ExternalFilesBody(BaseModel):
    """Whether the session may read files outside its workspace.

    ``StrictBool`` on purpose: the terminal's `/external-files` refuses anything
    that is not `allow` or `deny`, and a lax bool would read `"yes"` as consent to
    a permission change nobody spelled out.
    """

    allow: StrictBool


class DefaultEntryBody(BaseModel):
    """One defaults entry: the account, the model and the level it starts on.

    The three keys are the file's own — `agents.<name>` and `default_model` hold
    exactly these — so a client writes the shape the reader accepts instead of a
    translation of it.
    """

    account: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: str = 'default'


class ModelDefaultBody(BaseModel):
    """One default to write, and the file it belongs to.

    ``config`` is required and nullable on purpose: ``null`` clears the entry, which
    is the screen's `d`. Clearing therefore has to be spelled out, and cannot be what
    happens because a client forgot a field.
    """

    target: str | None = None
    """An agent name, or ``null`` for the block a conversation starts on."""
    scope: str = Field(min_length=1)
    """Which of the two files: ``project`` or ``global``."""
    config: DefaultEntryBody | None


# --- the app ----------------------------------------------------------------


class GracefulStop:
    """A one-way request to leave the serving loop, bound once the loop exists.

    The application is built before the server that runs it, and the server is
    what can stop, so neither can be handed to the other directly. This is the
    indirection: the app calls `request`, whoever runs the loop calls `bind`, and
    the request is a flag rather than an exit so the loop still unwinds in order —
    lifespan shutdown, endpoint removed, runtime closed, lock released.

    A request made before a binding is kept and applied at `bind`: a grace window
    shorter than the startup is legitimate configuration, and losing the request
    would leave the process serving forever.
    """

    def __init__(self) -> None:
        self._requested = False
        self._stop: Callable[[], None] | None = None

    @property
    def requested(self) -> bool:
        """Whether a stop has been asked for, bound or not."""
        return self._requested

    def bind(self, stop: Callable[[], None]) -> None:
        """Attaches the loop's own switch, applying a request already made."""
        self._stop = stop
        if self._requested:
            stop()

    def request(self) -> None:
        """Asks the serving loop to stop. Idempotent."""
        self._requested = True
        if self._stop is not None:
            self._stop()


def create_app(
    runtime: ServerRuntime,
    config: ServerConfig,
    *,
    dist: Path | None = None,
    publisher: EndpointPublisher | None = None,
    request_shutdown: Callable[[], None] | None = None,
    idle_grace: float = DEFAULT_IDLE_GRACE,
    idle_poll: float = IDLE_POLL_INTERVAL,
) -> FastAPI:
    """Builds the application around a runtime that is already wired.

    The runtime is passed in rather than built here so a test can hand over one with
    a stub model and assert on the routes without a workspace or a network.

    ``config.web`` selects the surface, so there is one source of truth for it: with
    it, `web/dist` is mounted and `/api/login` exchanges the password for a cookie;
    without it, the app is the API alone — token authentication, no page, no login
    route. ``dist`` overrides where the bundle is read from, which is what a test
    uses to point at a directory that does or does not exist; it is ignored when the
    surface has no web client.

    ``publisher`` is the file a local client reads to find this service. It is
    published from the lifespan below and removed when the lifespan ends, because
    the lifespan is the only hook that runs *after* the listening socket exists and
    *before* the process is gone. ``None`` means "this app is not a discoverable
    service": a test that mounts the app in-process publishes nothing.

    ``request_shutdown`` is how the service stops itself when there is no work left:
    it is called once, ``idle_grace`` seconds after the last turn ended and the last
    non-terminal queue row in the database was resolved, and it must end the serving
    loop gracefully rather than exit the process. ``None`` means nobody can stop this
    app — an in-process mount, or a runner that owns its own lifetime — and no watch
    is started at all, so a test never loses its server to a timer.
    """
    auth = Auth(config.password, service_token=publisher.token if publisher is not None else None)
    throttle = LoginThrottle()
    hub = runtime.hub
    # `None` is not "no bundle found", it is "this surface has no bundle": the API
    # has no page, no assets and no login form, and `dist` is ignored rather than
    # mounted by accident.
    assets = (dist if dist is not None else dist_root()) if config.web else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime.start()
        runtime.ensure_workers()
        # The port is bound and listening before this app is started: `src/server/cli.py`
        # binds the socket and hands it to uvicorn, so a client that reads the file is
        # never told about a port that is not there yet.
        if publisher is not None:
            publisher.publish(host=config.host, port=config.port)
        watch = (
            None
            if request_shutdown is None
            else asyncio.get_running_loop().create_task(
                runtime.stop_when_idle(request_shutdown, grace=idle_grace, poll=idle_poll)
            )
        )
        try:
            yield
        finally:
            # The watch stops first: a request to leave *while* the loop is already
            # leaving is at best noise, and at worst a second attempt on a runtime
            # that is unwinding.
            if watch is not None:
                watch.cancel()
                with suppress(asyncio.CancelledError):
                    await watch
            # Unpublish first: a client that finds the file while the server is closing
            # should fail to connect rather than attach to a runtime that is unwinding.
            if publisher is not None:
                publisher.clear()
            runtime.abort()
            await runtime.wait()
            runtime.composition.summoner.cancel_all()
            await runtime.composition.extensions.close()
            runtime.close()

    app = FastAPI(
        lifespan=lifespan,
        title='Sigil',
        version='0.1.0',
        docs_url='/api/docs',
        openapi_url='/api/openapi.json',
        redoc_url=None,
    )

    # Same-origin is the normal case, and CORS does not apply to it. These origins
    # exist for the one case that does: a client served from somewhere else (a Vite
    # dev server, a static host) talking to this API. They are not `*`: a wildcard
    # plus cookies is a combination browsers refuse and proxies tend to get wrong.
    if config.origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.origins,
            allow_credentials=True,
            allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
            allow_headers=['content-type', 'last-event-id'],
        )

    # `add_middleware` wraps in reverse order, so the *last* one added runs first.
    # The body ceiling is added before the cookie check on purpose: auth ends up
    # outermost, so an unauthenticated request is refused without a single byte of
    # its body being read. The cap only bounds what an authenticated caller may
    # send, which is the point — refusing early is a cost saving, not a boundary.
    app.add_middleware(BodyLimitMiddleware, limit=MAX_COMMAND_BYTES)
    app.add_middleware(AuthMiddleware, auth=auth)

    # --- session -------------------------------------------------------

    @app.get('/health')
    async def health() -> dict[str, Any]:
        """Unauthenticated liveness: what a load balancer and the login page need."""
        return {'status': 'ok', 'version': app.version}

    @app.get('/api/status')
    async def status() -> dict[str, Any]:
        """Unauthenticated: enough for a client to say what it is talking to.

        The bundle section belongs to the surface that serves one. A backend that
        reported on `web/dist` would be describing a file it never reads.
        """
        if assets is None:
            return {'version': app.version, 'authenticated': False, 'web': False}
        state = staleness(assets.parent if dist is not None else None)
        return {
            'version': app.version,
            'authenticated': False,
            'web': True,
            'bundle': {'stale': state.stale, 'reason': state.reason},
        }

    if config.web:

        @app.post('/api/login')
        async def login(body: LoginBody, request: Request) -> JSONResponse:
            """Exchanges the password for a signed cookie.

            Registered only on `sigil serve`: the API surface has no password and no
            browser, so there is nothing to exchange and no route to guess.
            """
            key = client_key(request.scope)
            blocked = throttle.blocked_for(key)
            if blocked > 0:
                return JSONResponse(
                    {'error': f'Too many attempts. Try again in {blocked:.0f}s.'},
                    status_code=429,
                    headers={'retry-after': str(int(blocked) + 1)},
                )
            if not auth.check_password(body.password):
                throttle.record_failure(key)
                delay = throttle.delay_for(key)
                if delay:
                    await asyncio.sleep(delay)
                return JSONResponse({'error': 'Wrong password.'}, status_code=401)
            throttle.reset(key)
            token = auth.issue()
            secure = request.url.scheme == 'https'
            response = JSONResponse({'ok': True})
            response.headers['set-cookie'] = session_cookie(auth, token, secure=secure)
            return response

        @app.post('/api/logout')
        async def logout() -> JSONResponse:
            response = JSONResponse({'ok': True})
            response.headers['set-cookie'] = cleared_cookie(auth)
            return response

    # --- state ---------------------------------------------------------

    @app.get('/api/state')
    async def state() -> dict[str, Any]:
        """Everything the screen needs, in one request.

        Sections are cheap queries and cached values, not a transcript: the
        conversation is its own route because it is the one that grows.
        """
        return await _state(runtime)

    @app.get('/api/session/transcript')
    async def transcript(limit: str | None = None, before: str | None = None) -> dict[str, Any]:
        """The conversation of the open session, as items a client can paint.

        Without ``limit`` the whole conversation comes back, which is what the web
        client still asks for. With it, the answer is one window: ``before`` is the
        position the page ends just before, and absent means the newest one. The
        items keep the ``index`` they already had, because a click maps that index to
        a rollback point and a window must not renumber a conversation.

        The newest page — the one asked for without ``before`` — also carries
        ``pending``, the rows waiting in the queue. A screen needs the conversation
        and the wait together, and asking for the whole transcript only to learn of a
        queued message is the cost this window exists to remove. An older page
        carries an empty list: a waiting row is not history, so it cannot ride a
        chunk and must not be painted twice.
        """
        try:
            limit_items = _page_limit(limit)
            before_position = _page_before(before)
        except ValueError as exc:
            return _refused(exc)  # type: ignore[return-value]
        # The window is projected from the messages, not cut from a projection of
        # the whole conversation: a page costs what the page holds, not what the
        # session has. This runs on the event loop, so the difference is every other
        # route's latency too.
        page = views.transcript_window(runtime.session.messages, limit=limit_items, before=before_position)
        return {
            'session': views.session_view(runtime.session),
            'items': page.items,
            'first_index': page.first_index,
            'has_more': page.has_more,
            'total': page.total,
            'skills': views.skills_view(runtime.session.messages),
            'pending': []
            if before_position is not None
            else views.pending_rows(runtime.composition.store.database, runtime.session.id),
        }

    @app.get('/api/session/durable')
    async def durable() -> dict[str, Any]:
        """The durable transcript, queue overlay and recovery facts in one read.

        A session that exists only in memory has no row to project. That is a typed
        404 — "nothing yet" — and not a failure: a terminal attached to a conversation
        that has not started is in a normal state, and answering 500 painted an error
        banner about mismatched versions on a service that was answering correctly.
        """
        try:
            return views.durable_session(runtime.composition.store.database, runtime.session.id)
        except views.SessionNotStored as exc:
            return JSONResponse({'error': str(exc)}, status_code=404)  # type: ignore[return-value]

    @app.get('/api/sessions')
    async def sessions() -> dict[str, Any]:
        """Every conversation of this repository's workspaces, newest first."""
        root = runtime.composition.store.cwd
        repository = GitRepository.discover(root)
        entries = catalog(root)
        return {
            'current': runtime.session.id,
            'sessions': [
                {
                    **views.summary_view(
                        entry.session,
                        repository=repository.name if repository is not None else None,
                        is_worktree=entry.workspace.is_worktree,
                    ),
                    'workspace_label': entry.label,
                    'is_current': entry.is_current,
                }
                for entry in entries
            ],
        }

    @app.get('/api/workspaces')
    async def workspaces() -> dict[str, Any]:
        """The workspaces of this repository, grouped: the main checkout and its worktrees.

        One group, because the picker only ever shows the repository Sigil is in. The
        route is shaped as a list anyway: switching to another project root is a
        different feature, and pretending it is already here would be a lie in the
        response.
        """
        active = runtime.composition.workspace_manager.current()
        current_root = Path(os.path.realpath(str(active.root)))
        repository = GitRepository.discover(current_root)
        found = discover_workspaces(current_root)
        return {
            'repositories': views.repositories_view(
                repository.name if repository is not None else None,
                str(repository.main_root) if repository is not None else None,
                found,
                active=str(current_root),
            )
        }

    @app.get('/api/mcp')
    async def mcp() -> dict[str, Any]:
        return views.mcp_view(await runtime.composition.mcp.describe())

    # --- actions -------------------------------------------------------

    @app.post('/api/turn')
    async def turn(body: TurnBody) -> dict[str, Any]:
        """Sends a message: a new turn, or a steer into the running one.

        The images travel validated and ordered; a refusal is a `ValueError` from the
        runtime, which becomes a 400 with the reason and leaves the queue untouched.
        """
        try:
            return await runtime.send(
                body.text,
                request_id=body.request_id,
                images=[IncomingImage(media_type=image.media_type, data=image.data) for image in body.images],
            )
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)  # type: ignore[return-value]

    @app.post('/api/abort')
    async def abort() -> dict[str, Any]:
        return {'stopped': runtime.abort()}

    @app.post('/api/stop-all')
    async def stop_all() -> dict[str, Any]:
        """Stops the turn and anything else still running: the exit path's request."""
        return {'stopped': runtime.stop_all()}

    @app.post('/api/answer')
    async def answer(body: AnswerBody) -> dict[str, Any]:
        resolved = runtime.answer(body.id, body.answers)
        return {'resolved': resolved}

    @app.post('/api/session/new')
    async def new_session(body: NewSessionBody | None = None) -> dict[str, Any]:
        """Starts an empty conversation, in a worktree cut first when one is named.

        The order is the engine's: create the worktree, then start the session, so a
        name git refuses leaves the service where it was instead of half moved.
        """
        worktree = '' if body is None else (body.worktree or '').strip()
        reply: dict[str, Any] = {}
        if worktree:
            try:
                switch = await asyncio.to_thread(runtime.create_worktree, worktree)
            except RuntimeError as exc:
                return _conflict(exc)  # type: ignore[return-value]
            except (WorkspaceError, OSError) as exc:
                return _refused(exc)  # type: ignore[return-value]
            reply['workspace'] = views.workspace_view(switch.workspace)
        try:
            session = runtime.new_session()
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        reply['session'] = views.session_view(session)
        return reply

    @app.post('/api/worktree')
    async def create_worktree(body: WorktreeBody) -> dict[str, Any]:
        """Cuts a worktree and moves the service into it.

        The engine half is `WorkspaceManager.create_and_switch` — the very call the
        in-process `/new --worktree` makes — so the two clients cut the same tree the
        same way and a failure leaves the repository and the session untouched.
        """
        try:
            runtime.refuse_while_busy('create a worktree')
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        try:
            switch = await asyncio.to_thread(runtime.create_worktree, body.name)
        except (WorkspaceError, OSError) as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {
            'workspace': views.workspace_view(switch.workspace),
            'previous': views.workspace_view(switch.previous),
        }

    @app.post('/api/session/open')
    async def open_session(body: SessionBody) -> dict[str, Any]:
        try:
            session = runtime.open_session(body.id)
        except (RuntimeError, LookupError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)  # type: ignore[return-value]
        return {'session': views.session_view(session)}

    @app.post('/api/workspace')
    async def switch_workspace(body: WorkspaceBody) -> dict[str, Any]:
        try:
            result = runtime.switch_workspace(body.root)
        except (RuntimeError, WorkspaceError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)  # type: ignore[return-value]
        return result

    @app.get('/api/models')
    async def models() -> dict[str, Any]:
        """The accounts and models known to the router, so a client can offer them."""
        service = runtime.composition.model_service
        return {
            'current': views.model_view(service.snapshot()),
            'accounts': [
                {'id': account.id, 'alias': account.alias, 'kind': str(account.kind)}
                for account in service.accounts.load()
            ],
        }

    @app.get('/api/models/{alias}')
    async def account_models(alias: str) -> dict[str, Any]:
        """The catalogue of one account, with a bounded wait.

        The picker needs the models of the account it is about to switch, and they
        are a network call on the provider's side. The bound is here and not only in
        the transport so the route cannot hang a client: a provider that never
        answers becomes a refusal with a sentence, not a request that stays open.

        An account the router does not know is a request that cannot be satisfied.
        A catalogue the provider could not answer for is reported as its own refusal
        with the engine's reason — the fallback list is deliberately not offered as
        if it were the provider's, because a picker that shows invented options is
        the failure this route exists to avoid.
        """
        service = runtime.composition.model_service
        try:
            catalog = await asyncio.wait_for(service.catalog_for(alias), timeout=CATALOG_TIMEOUT_SECONDS)
        except TimeoutError:
            return _refused(
                RuntimeError(
                    f'The model list of {alias!r} did not answer within '
                    f'{CATALOG_TIMEOUT_SECONDS:.0f}s. Check the network and try /model again.'
                )
            )  # type: ignore[return-value]
        except ProviderError as exc:
            return _refused(exc)  # type: ignore[return-value]
        if catalog.error is not None and not catalog.models:
            return _refused(RuntimeError(f'{alias}: {catalog.error}'))  # type: ignore[return-value]
        return {'account': alias, 'catalog': views.catalog_view(catalog)}

    @app.get('/api/accounts')
    async def accounts() -> dict[str, Any]:
        """Every configured account, and whether it holds a credential. Never the key."""
        return {'accounts': _account_rows(runtime.composition.model_service)}

    @app.post('/api/accounts')
    async def add_account(body: AccountBody) -> dict[str, Any]:
        """Adds an API-key account and makes it active, refused while a turn runs."""
        service = runtime.composition.model_service
        if body.auth != AuthMethod.API_KEY.value:
            return _refused(RuntimeError(OAUTH_OVER_HTTP))  # type: ignore[return-value]
        try:
            kind = ProviderKind(body.provider)
        except ValueError:
            known = ', '.join(item.value for item in ProviderKind)
            return _refused(RuntimeError(f'Unknown provider {body.provider!r}. Known providers: {known}.'))  # type: ignore[return-value]
        if AuthMethod.API_KEY not in auth_methods_for(kind):
            return _refused(RuntimeError(OAUTH_OVER_HTTP))  # type: ignore[return-value]
        key = (body.api_key or '').strip()
        if not key:
            return _refused(RuntimeError('An API key is required to add an account from this client.'))  # type: ignore[return-value]
        try:
            runtime.refuse_while_busy('add a provider account')
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        try:
            snapshot = await asyncio.wait_for(
                service.add_api_key(kind, body.alias, key), timeout=ACCOUNT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return _refused(
                RuntimeError(
                    f'The credential check for {body.alias!r} did not answer within '
                    f'{ACCOUNT_TIMEOUT_SECONDS:.0f}s. Check the network and try again.'
                )
            )  # type: ignore[return-value]
        except ProviderError as exc:
            return _refused(exc)  # type: ignore[return-value]
        runtime.adopt_model(snapshot)
        return {'accounts': _account_rows(service), 'model': views.model_view(snapshot)}

    @app.post('/api/accounts/{alias}/activate')
    async def activate_account(alias: str) -> dict[str, Any]:
        """Makes an account active, using the model it last had."""
        service = runtime.composition.model_service
        try:
            runtime.refuse_while_busy('activate a provider account')
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        try:
            account = service.find(alias)
            model = account.last_model
            if not model:
                catalog = await asyncio.wait_for(service.catalog_for(alias), timeout=CATALOG_TIMEOUT_SECONDS)
                model = catalog.models[0].name if catalog.models else ''
            if not model:
                return _refused(RuntimeError(f'{alias} has no model to activate yet. Pick one with /model first.'))  # type: ignore[return-value]
            snapshot = runtime.set_model(alias, model)
        except TimeoutError:
            return _refused(RuntimeError(f'The model list of {alias!r} did not answer in time.'))  # type: ignore[return-value]
        except BusyError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        except ProviderError as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {'accounts': _account_rows(service), 'model': views.model_view(snapshot)}

    @app.delete('/api/accounts/{alias}')
    async def delete_account(alias: str) -> dict[str, Any]:
        """Removes an account and its credential.

        Two rules, both refusals with a reason: the **last** account cannot go (the
        session would have nothing to run), and neither can the **active** one. The
        engine promotes a successor when the active account is removed, but only a
        person at the terminal sees the model change under their feet; an API caller
        would switch models mid-session without asking. Activating another account
        first is the explicit step, and this route insists on it.
        """
        service = runtime.composition.model_service
        try:
            runtime.refuse_while_busy('delete a provider account')
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        try:
            account = service.find(alias)
            if any(view.active and view.account.id == account.id for view in service.account_views()):
                return _refused(
                    RuntimeError(f'{alias} is the active account. Activate another one before deleting it.')
                )  # type: ignore[return-value]
            service.remove(alias)
        except ProviderError as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {'accounts': _account_rows(service)}

    @app.post('/api/accounts/{alias}/rename')
    async def rename_account(alias: str, body: RenameBody) -> dict[str, Any]:
        """Changes an account's label. Its id, and saved sessions, stay valid."""
        service = runtime.composition.model_service
        try:
            runtime.refuse_while_busy('rename a provider account')
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        try:
            service.rename(alias, body.new_alias)
        except ProviderError as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {'accounts': _account_rows(service)}

    @app.post('/api/model')
    async def change_model(body: ModelBody) -> dict[str, Any]:
        """Switches the model of the session, refused while a turn runs.

        The refusal is the engine's (`BusyError` from `ModelService.select`), which
        is the one the terminal gets from the same call. Any other provider failure
        — an unknown account, a model the catalogue does not know, a level the
        model does not accept — is a request that cannot be satisfied.
        """
        try:
            snapshot = runtime.set_model(body.account, body.model, body.thinking or 'default')
        except BusyError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        except ProviderError as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {'model': views.model_view(snapshot)}

    @app.post('/api/effort')
    async def change_effort(body: EffortBody) -> dict[str, Any]:
        """Sets the reasoning level, effective on the next step and not this one."""
        try:
            snapshot = runtime.set_effort(body.effort)
        except BusyError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        except ProviderError as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {'model': views.model_view(snapshot)}

    @app.post('/api/agent')
    async def change_agent(body: AgentBody) -> dict[str, Any]:
        """Activates an agent profile for the next turn, refused while one runs."""
        try:
            profile = runtime.select_agent(body.name)
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        except (AgentNotFound, ProviderError) as exc:
            return _refused(exc)  # type: ignore[return-value]
        registry = runtime.composition.agent_registry
        return {
            'agent': views.agent_view(profile, active=True),
            'agents': [views.agent_view(item, active=item.name == registry.active_name) for item in registry.list()],
        }

    @app.get('/api/rollback')
    async def rollback_points() -> dict[str, Any]:
        """The turns the open session can return to, newest first."""
        composition = runtime.composition
        return {
            'points': views.rollback_points(composition.store.database, runtime.rollback_points()),
        }

    @app.post('/api/rollback')
    async def rollback(body: RollbackBody) -> dict[str, Any]:
        """Returns the session to before a turn, rows and optionally files.

        Off the event loop: the filesystem half diffs and restores the workspace,
        and a stream must keep flowing while it does. The reply carries the session
        projection because the transcript is shorter now and the client has to
        repaint from the new truth.
        """
        # Read before the rows are truncated: the prompt lives in the messages the
        # rollback drops, which is why the terminal used to look it up beforehand.
        # Doing it here means every client gets it, and none has to own a database.
        prompt = prompt_of_turn(runtime.composition.store.database, body.turn_id)
        try:
            outcome = await asyncio.to_thread(runtime.rollback, body.turn_id, files=body.files)
        except (RuntimeError, LookupError, RollbackError) as exc:
            return _conflict(exc)  # type: ignore[return-value]
        return {
            'rollback': views.rollback_view(
                outcome,
                prompt=None if prompt is None else prompt[0],
                prompt_images=[] if prompt is None else [_image_payload(image) for image in prompt[1]],
            ),
            'session': views.session_view(runtime.session),
        }

    @app.post('/api/fork')
    async def fork(body: ForkBody) -> dict[str, Any]:
        """Branches a new session at the instant before a turn. Nothing is destroyed."""
        try:
            outcome = await asyncio.to_thread(runtime.fork, body.turn_id)
        except (RuntimeError, LookupError, RollbackError) as exc:
            return _conflict(exc)  # type: ignore[return-value]
        return {'fork': views.fork_view(outcome)}

    # --- the tree ------------------------------------------------------

    @app.post('/api/tree/stop')
    async def stop_node(body: NodeBody) -> dict[str, Any]:
        """Stops a subagent and everything it summoned.

        The refusals are the terminal's: an id that names no node is a request
        that cannot be satisfied, and the session's own agent is not a subagent.
        """
        try:
            node, message = runtime.stop_node(body.node_id)
        except LookupError as exc:
            return _refused(exc)  # type: ignore[return-value]
        except SummonError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        return _tree_reply(runtime, node, message)

    @app.post('/api/tree/background')
    async def background_node(body: NodeBody) -> dict[str, Any]:
        """Sends a running subagent to the background: it answers as a message later."""
        try:
            node, message = runtime.background_node(body.node_id)
        except LookupError as exc:
            return _refused(exc)  # type: ignore[return-value]
        except SummonError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        return _tree_reply(runtime, node, message)

    @app.get('/api/tree/node/{node_id}')
    async def node_transcript(node_id: str) -> dict[str, Any]:
        """The messages of one execution, as the terminal's subagent view paints them.

        The root keeps no messages of its own: its conversation is the session's,
        and `/api/session/transcript` is where it is read.
        """
        try:
            node = runtime.node(node_id)
        except LookupError as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {'node': views.node_view(node, include_messages=True)}

    # --- session settings ----------------------------------------------

    @app.post('/api/mcp')
    async def toggle_mcp(body: McpBody) -> dict[str, Any]:
        """Enables or disables one MCP server for this session, and remembers it.

        Refused mid-turn and while a subagent runs, the two criteria the terminal
        applies to `/mcp`.
        """
        try:
            session = await runtime.toggle_mcp(body.name, body.enabled)
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        except LookupError as exc:
            return _refused(exc)  # type: ignore[return-value]
        return {'session': views.session_view(session), 'mcp': await _mcp_state(runtime.composition)}

    @app.post('/api/external-files')
    async def external_files(body: ExternalFilesBody) -> dict[str, Any]:
        """Allows or denies file access outside the workspace for this session.

        Refused mid-turn, like the terminal's `/external-files`.
        """
        try:
            session = runtime.set_external_files(body.allow)
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        return {'session': views.session_view(session)}

    @app.get('/api/context')
    async def context() -> dict[str, Any]:
        """How full the window is and what the session has cost, from the meter.

        The measurement is of the session's own history, which the turn loop
        checkpoints as it advances; a running turn's newest steps can therefore be
        a moment behind the transcript the stream is painting.
        """
        meter = runtime.composition.meter
        messages = runtime.session.messages
        return {'context': views.context_view(meter.snapshot(messages), session_cost(messages))}

    # --- the model defaults --------------------------------------------

    @app.get('/api/model-defaults')
    async def model_defaults() -> dict[str, Any]:
        """Both config files that decide what a profile starts on, per file.

        Reading is never refused, mid-turn included: the screen shows the state
        before the user edits it, and looking at the files changes nothing.
        """
        service = runtime.composition.model_service
        return {'defaults': _defaults_payload(service, runtime.model_defaults())}

    @app.post('/api/model-defaults')
    async def write_model_default(body: ModelDefaultBody) -> dict[str, Any]:
        """Sets or clears one default and answers with the state the write left.

        The write is the engine's (`Composition.save_model_default`), and so are the
        refusals: a turn in the way — the rule the terminal's `/default-models`
        already applies — or a config file that cannot be read and is therefore not
        rewritten, is a ``409`` carrying the engine's own sentence. A request naming
        a destination, an agent or a level Sigil does not have is a ``400``, because
        a different request would work.
        """
        try:
            scope = ConfigScope(body.scope)
        except ValueError:
            known = ', '.join(item.value for item in ConfigScope)
            return _refused(RuntimeError(f'Unknown defaults destination {body.scope!r}. Known destinations: {known}.'))  # type: ignore[return-value]
        if body.target is not None and not is_valid_agent_name(body.target):
            return _refused(RuntimeError(f'{body.target!r} is not an agent name.'))  # type: ignore[return-value]
        config: AgentModelConfig | None = None
        if body.config is not None:
            effort = parse_choice(body.config.effort)
            if effort is None:
                known = ', '.join(EFFORT_LEVELS)
                return _refused(RuntimeError(f'Unknown effort {body.config.effort!r}. Known levels: {known}.'))  # type: ignore[return-value]
            config = AgentModelConfig(body.config.account, body.config.model, effort)
        try:
            state = runtime.save_model_default(body.target, config, scope)
        except RuntimeError as exc:
            return _conflict(exc)  # type: ignore[return-value]
        return {'defaults': _defaults_payload(runtime.composition.model_service, state)}

    # --- events --------------------------------------------------------

    @app.get('/events')
    async def events(request: Request) -> StreamingResponse:
        """The stream: `seq`-numbered events, replayable from a cookie or a header."""
        after = _resume_from(request)
        return StreamingResponse(
            _stream(hub, request, after),
            media_type='text/event-stream',
            headers={
                'cache-control': 'no-cache, no-transform',
                'x-accel-buffering': 'no',
                'connection': 'keep-alive',
            },
        )

    # --- the client ----------------------------------------------------

    if assets is None:

        @app.get('/')
        async def index_backend() -> Response:
            """The API has no page, and says so instead of answering a mute 404."""
            return JSONResponse({'service': 'sigil backend', 'web': False, 'message': BACKEND_NOTICE})

    elif assets.is_dir():
        app.mount('/static', StaticFiles(directory=assets / 'assets'), name='static')

        @app.get('/')
        async def index() -> Response:
            page = assets / 'index.html'
            if not page.is_file():
                return JSONResponse({'error': 'the web client is not built'}, status_code=503)
            return FileResponse(page, media_type='text/html')

        @app.get('/{path:path}')
        async def spa(path: str) -> Response:
            """Fallback for the client's own routes.

            A real file wins; otherwise the entry point, so a reload on `/chat/xyz`
            paints the app instead of a 404. An unknown path under `/api` does not get
            the HTML: a client asking for JSON must never receive the page.
            """
            if path.startswith('api/'):
                return JSONResponse({'error': 'not found'}, status_code=404)
            candidate = assets / path
            if candidate.is_file() and assets in candidate.resolve().parents:
                guessed, _ = mimetypes.guess_type(candidate.name)
                return FileResponse(candidate, media_type=guessed)
            page = assets / 'index.html'
            if page.is_file():
                return FileResponse(page, media_type='text/html')
            return JSONResponse({'error': 'the web client is not built'}, status_code=503)
    else:

        @app.get('/')
        async def index_missing() -> Response:
            state = staleness()
            return JSONResponse(
                {'error': 'the web client is not built', 'reason': state.reason, 'hint': state.message()},
                status_code=503,
            )

    return app


def _tree_reply(runtime: ServerRuntime, node: Any, message: str) -> dict[str, Any]:
    """A tree action's reply: the node, the tree it lives in, and the engine's sentence.

    The tree travels with the node because stopping a node stops everything under
    it: a client that painted only the row would leave its children saying they
    are still working.
    """
    return {
        'node': views.node_view(node),
        'tree': views.tree_view(runtime.composition.agent_trees.active),
        'message': message,
    }


async def _state(runtime: ServerRuntime) -> dict[str, Any]:
    """The projection the client renders the shell from."""
    composition = runtime.composition
    session = composition.session
    tree = composition.agent_trees.active
    profile = composition.agent_registry.active
    return {
        'session': views.session_view(session),
        'workspace': views.workspace_view(
            composition.workspace_manager.current(),
            repository=_repository_name(composition),
        ),
        'model': views.model_view(composition.model_service.snapshot()),
        'agent': None if profile is None else views.agent_view(profile, active=True),
        'agents': [
            views.agent_view(item, active=item.name == composition.agent_registry.active_name)
            for item in composition.agent_registry.list()
        ],
        'tree': views.tree_view(tree),
        'roadmap': views.roadmap_view(composition.roadmap_store.root.snapshot()),
        'busy': runtime.busy,
        'question': None
        if runtime.pending_question is None
        else {
            'id': runtime.pending_question.id,
            'questions': [
                {
                    'question': item.question,
                    'allow_custom': item.allow_custom,
                    'options': [
                        {'label': option.label, 'description': option.description, 'recommended': option.recommended}
                        for option in item.options
                    ],
                }
                for item in runtime.pending_question.questions
            ],
        },
        'mcp': await _mcp_state(composition),
        'skills': views.skills_view(session.messages),
        'seq': runtime.hub.last_seq,
    }


def _image_payload(image: Any) -> dict[str, Any]:
    """One restored image, base64 so it survives JSON. The bytes, not a path.

    A rollback gives the prompt back the way it was sent, picture included: a
    message whose text returns without its image points at an `[Image #1]` that no
    longer exists.
    """
    return {
        'data': base64.b64encode(image.data).decode('ascii'),
        'media_type': image.media_type or 'application/octet-stream',
        'identifier': image.identifier,
    }


def _defaults_payload(service: Any, state: Any) -> dict[str, Any]:
    """The defaults projection, with the accounts its entries name.

    The accounts come from the service that has them, not from the state: the state
    knows the aliases the files hold, and only the router knows which of those still
    exist.
    """
    return views.model_defaults_view(state, accounts=service.list_accounts())


def _account_rows(service: Any) -> list[dict[str, Any]]:
    """The accounts as the screen paints them, with no credential in sight."""
    return [
        views.account_view(
            view.account,
            active=view.active,
            has_credential=service.secrets.get(view.account.id) is not None,
        )
        for view in service.account_views()
    ]


def _repository_name(composition: Any) -> str | None:
    """The git repository the active workspace belongs to, or ``None`` outside git.

    It is what the header needs to say `sigil › better-debug`: a worktree is a
    workspace, and the repository is the project it still belongs to.
    """
    repository = GitRepository.discover(composition.store.cwd)
    return None if repository is None else repository.name


async def _mcp_state(composition: Any) -> dict[str, Any]:
    """MCP status for the shell, without waiting for a slow server to start.

    `describe()` waits for the layer, which is right for the diagnostic route and
    wrong here: the page must paint while `npx` is still resolving. A gateway that
    has not loaded reports `loading` and an empty list, which is the truth.
    """
    gateway = composition.mcp
    if not gateway.loaded:
        return {'servers': [], 'loading': True, 'errors': list(gateway.config_errors)}
    return views.mcp_view(await gateway.describe())


def _resume_from(request: Request) -> int:
    """Where the stream starts: the browser's `Last-Event-ID`, or `?after=`.

    Both are checked because the two reconnection paths are different: `EventSource`
    resends `Last-Event-ID` by itself, and a client that reconnects by hand passes the
    query parameter. A malformed value starts from now instead of failing the stream.
    """
    raw = request.headers.get('last-event-id') or request.query_params.get('after') or '0'
    try:
        return max(0, int(raw))
    except TypeError, ValueError:
        return 0


async def _stream(hub: EventHub, request: Request, after: int) -> AsyncIterator[bytes]:
    """Server-sent events: replay, then live, with a heartbeat on idle."""
    async with hub.subscribe() as subscription:
        # Replay inside the subscription, so nothing published between the two is lost.
        for event in hub.replay(after):
            yield event.to_sse().encode()
        while True:
            if await request.is_disconnected():
                return
            event = await subscription.next(timeout=HEARTBEAT_SECONDS)
            if event is None:
                yield b': keep-alive\n\n'
                continue
            yield event.to_sse().encode()


def encode(event: Any) -> str:
    """The wire form of an event, for tests and for anything else that needs it."""
    return json.dumps(event, ensure_ascii=False)


__all__ = ['GracefulStop', 'create_app', 'encode']
