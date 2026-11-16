"""Coordinate request admission with an automatic service shutdown."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.server.runtime.client_presence import ClientPresence


class ClientAdmissionMiddleware:
    def __init__(self, app: ASGIApp, *, presence: ClientPresence) -> None:
        self.app = app
        self.presence = presence

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        if not self.presence.begin_request():
            await JSONResponse({'error': 'service stopping'}, status_code=503)(scope, receive, send)
            return
        if scope.get('path') == '/events':
            # The stream can live for hours; the client lease, not its response,
            # owns liveness after the request has been admitted.
            self.presence.end_request()
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.presence.end_request()
