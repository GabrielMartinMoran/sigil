"""Pin a resident session for the complete HTTP or SSE response lifetime."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode

from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from src.server.server_runtime import ServerRuntime


class SessionLeaseMiddleware:
    def __init__(self, app: ASGIApp, *, runtime: ServerRuntime) -> None:
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http':
            query = parse_qs(scope.get('query_string', b'').decode('ascii', errors='replace'))
            session_id = query.get('session_id', [''])[0]
            if not session_id and 'session_id' not in query:
                headers = dict(scope.get('headers', []))
                session_id = headers.get(b'x-sigil-session', b'').decode('utf-8')
                if session_id:
                    scope = dict(scope)
                    scope['query_string'] = urlencode({**query, 'session_id': [session_id]}, doseq=True).encode()
            if session_id and self.runtime.find_session(session_id) is not None:
                with self.runtime.session_lease(session_id):
                    await self.app(scope, receive, send)
                return
        await self.app(scope, receive, send)
