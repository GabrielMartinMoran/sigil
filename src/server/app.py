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
import json
import mimetypes
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.engine.sessions.catalog import catalog
from src.engine.sessions.catalog import workspaces as discover_workspaces
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.engine.workspace.repository import GitRepository
from src.server import views
from src.server.auth import Auth, AuthMiddleware, LoginThrottle, cleared_cookie, client_key, session_cookie
from src.server.config import ServerConfig
from src.server.endpoint import EndpointPublisher
from src.server.events import EventHub
from src.server.runtime import IncomingImage, ServerRuntime
from src.server.webbuild import dist_root, staleness

HEARTBEAT_SECONDS = 20.0
"""How often an idle stream says something, so a proxy does not close it."""

MAX_COMMAND_BYTES = 64 * 1024 * 1024
"""Ceiling for one JSON command body, counted as the bytes arrive.

Ten images at the per-image limit are about 53 MiB once base64 adds a third; the
rest is the text and the envelope. A request over this is refused before it is
parsed, so an accidental upload cannot become a memory problem first.
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


# --- payloads ---------------------------------------------------------------


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


# --- the app ----------------------------------------------------------------


def create_app(
    runtime: ServerRuntime,
    config: ServerConfig,
    *,
    dist: Path | None = None,
    publisher: EndpointPublisher | None = None,
) -> FastAPI:
    """Builds the application around a runtime that is already wired.

    The runtime is passed in rather than built here so a test can hand over one with
    a stub model and assert on the routes without a workspace or a network.

    ``publisher`` is the file a local client reads to find this service. It is
    published from the lifespan below and removed when the lifespan ends, because
    the lifespan is the only hook that runs *after* the listening socket exists and
    *before* the process is gone. ``None`` means "this app is not a discoverable
    service": a test that mounts the app in-process publishes nothing.
    """
    auth = Auth(config.password, service_token=publisher.token if publisher is not None else None)
    throttle = LoginThrottle()
    hub = runtime.hub
    assets = dist if dist is not None else dist_root()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime.start()
        runtime.ensure_workers()
        # The port is bound and listening before this app is started: `src/server/cli.py`
        # binds the socket and hands it to uvicorn, so a client that reads the file is
        # never told about a port that is not there yet.
        if publisher is not None:
            publisher.publish(host=config.host, port=config.port)
        try:
            yield
        finally:
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
        """Unauthenticated: enough for the login page to say what it is talking to."""
        state = staleness(assets.parent if dist is not None else None)
        return {
            'version': app.version,
            'authenticated': False,
            'bundle': {'stale': state.stale, 'reason': state.reason},
        }

    @app.post('/api/login')
    async def login(body: LoginBody, request: Request, response: Response) -> JSONResponse:
        """Exchanges the password for a signed cookie."""
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
    async def transcript() -> dict[str, Any]:
        """The conversation of the open session, as items a client can paint."""
        session = runtime.session
        return {
            'session': views.session_view(session),
            'items': views.transcript(session.messages),
            'skills': views.skills_view(session.messages),
        }

    @app.get('/api/session/durable')
    async def durable() -> dict[str, Any]:
        """The durable transcript, queue overlay and recovery facts in one read."""
        return views.durable_session(runtime.composition.store.database, runtime.session.id)

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

    @app.post('/api/answer')
    async def answer(body: AnswerBody) -> dict[str, Any]:
        resolved = runtime.answer(body.id, body.answers)
        return {'resolved': resolved}

    @app.post('/api/session/new')
    async def new_session() -> dict[str, Any]:
        try:
            session = runtime.new_session()
        except RuntimeError as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)  # type: ignore[return-value]
        return {'session': views.session_view(session)}

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

    if assets.is_dir():
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


__all__ = ['create_app', 'encode']
