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

from src.engine.sessions.catalog import catalog
from src.engine.sessions.catalog import workspaces as discover_workspaces
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.engine.workspace.repository import GitRepository
from src.server import views
from src.server.auth import Auth, AuthMiddleware, LoginThrottle, cleared_cookie, client_key, session_cookie
from src.server.config import ServerConfig
from src.server.events import EventHub
from src.server.runtime import ServerRuntime
from src.server.webbuild import dist_root, staleness

HEARTBEAT_SECONDS = 20.0
"""How often an idle stream says something, so a proxy does not close it."""


# --- payloads ---------------------------------------------------------------


class LoginBody(BaseModel):
    """The only thing the login form sends."""

    password: str = Field(min_length=1)


class TurnBody(BaseModel):
    """A message from the composer."""

    text: str = Field(min_length=1)


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


def create_app(runtime: ServerRuntime, config: ServerConfig, *, dist: Path | None = None) -> FastAPI:
    """Builds the application around a runtime that is already wired.

    The runtime is passed in rather than built here so a test can hand over one with
    a stub model and assert on the routes without a workspace or a network.
    """
    auth = Auth(config.password)
    throttle = LoginThrottle()
    hub = runtime.hub
    assets = dist if dist is not None else dist_root()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime.abort()
            await runtime.wait()
            runtime.composition.summoner.cancel_all()
            await runtime.composition.extensions.close()

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

    # `add_middleware` wraps in reverse order, so this one runs first: nothing
    # reaches a route before the cookie is checked.
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
        """Sends a message: a new turn, or a steer into the running one."""
        try:
            return await runtime.send(body.text)
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
