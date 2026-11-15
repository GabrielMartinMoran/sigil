"""Reject unauthenticated API requests before the app sees them."""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from src.server.auth import Auth, _deny, _is_free


class AuthMiddleware:
    """Rejects an unauthenticated request to the API before the app sees it."""

    def __init__(self, app: ASGIApp, auth: Auth) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] not in {'http', 'websocket'}:
            await self.app(scope, receive, send)
            return
        path = str(scope.get('path', '/'))
        if path == '/events':
            # `/events` is not an `/api` path but it is the conversation streaming
            # out of the server: it needs the same session the rest of the API does.
            if not self.auth.authenticated(scope):
                await _deny(send)
                return
            await self.app(scope, receive, send)
            return
        if not path.startswith('/api/') or _is_free(path):
            await self.app(scope, receive, send)
            return
        if not self.auth.authenticated(scope):
            await _deny(send)
            return
        await self.app(scope, receive, send)
