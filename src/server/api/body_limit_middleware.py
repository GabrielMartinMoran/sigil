"""Rejects a request body larger than the command ceiling."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_COMMAND_BYTES = 64 * 1024 * 1024


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
