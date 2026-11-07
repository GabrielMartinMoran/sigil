"""The password gate in front of the web client.

Three decisions worth writing down:

- **The page is public, the API is not.** `index.html` and its assets carry no
  conversation and no credential; what they carry is the form that asks for the
  password. Gating the page would leave a browser with a download prompt and
  nowhere to type, so the gate is on `/api/*` and `/events` instead.
- **The cookie is not the password.** What the browser keeps is an HMAC of the
  issue time under a key that is generated for the process and never written
  down. It stops being valid on restart, which is the property that matters for a
  local tool: `ctrl+c` is a logout. The password itself never leaves the server.
- **Guessing has a cost.** A wrong password is answered with a delay that grows
  with the number of failures from the same client, and the throttle is in
  memory because there is one process.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = 'sigil_session'
"""Name of the cookie the browser keeps. No `__Host-` prefix: TLS is the deployment's."""

SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
"""How long a login lasts. One server restart ends it anyway: the key is per process."""

FREE_PATHS = frozenset({'/', '/index.html', '/favicon.ico', '/health', '/api/login', '/api/status'})
"""Paths a browser reaches without a session. The rest of `/api` needs one."""

FREE_PREFIXES = ('/static/',)
"""Asset paths: needed to paint the login form before there is a cookie."""

FAILURE_WINDOW_SECONDS = 60.0
MAX_FAILURES_PER_WINDOW = 10
MAX_DELAY_SECONDS = 1.0


def _digest(value: str) -> bytes:
    return value.encode('utf-8')


class Auth:
    """Password check plus the signed cookie that follows a successful one."""

    def __init__(
        self,
        password: str,
        *,
        cookie_name: str = COOKIE_NAME,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        signing_key: bytes | None = None,
    ) -> None:
        self._password = password
        self.cookie_name = cookie_name
        self.ttl_seconds = ttl_seconds
        # Per process on purpose: no file to leak, and a restart revokes every session.
        self._key = signing_key if signing_key is not None else secrets.token_bytes(32)

    # --- the password -------------------------------------------------------

    def check_password(self, value: object) -> bool:
        """Constant-time comparison against the configured password."""
        if not isinstance(value, str) or not value:
            return False
        return hmac.compare_digest(_digest(value), _digest(self._password))

    # --- the cookie ---------------------------------------------------------

    def issue(self, *, now: float | None = None) -> str:
        """A token for the session cookie: issue time plus its signature."""
        stamp = f'{int(now if now is not None else time.time())}'
        return f'{stamp}.{self._sign(stamp)}'

    def verify(self, token: object, *, now: float | None = None) -> bool:
        """Whether ``token`` was issued here and has not expired."""
        if not isinstance(token, str) or '.' not in token:
            return False
        stamp, _, signature = token.partition('.')
        if not stamp.isdigit():
            return False
        if not hmac.compare_digest(signature, self._sign(stamp)):
            return False
        issued = int(stamp)
        current = time.time() if now is None else now
        return 0 <= current - issued <= self.ttl_seconds

    def _sign(self, stamp: str) -> str:
        return hmac.new(self._key, _digest(f'sigil-web-session:{stamp}'), hashlib.sha256).hexdigest()[:32]

    # --- helpers for the routes ---------------------------------------------

    def token_from(self, scope: Scope) -> str | None:
        """The session token carried by a request, if any."""
        headers = Headers(scope=scope)
        raw = headers.get('cookie')
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 - a malformed cookie is simply no cookie
            return None
        morsel = jar.get(self.cookie_name)
        return None if morsel is None else morsel.value

    def authenticated(self, scope: Scope) -> bool:
        return self.verify(self.token_from(scope))


class LoginThrottle:
    """Growing delay and a hard stop for repeated wrong passwords, per client."""

    def __init__(
        self,
        *,
        window_seconds: float = FAILURE_WINDOW_SECONDS,
        max_failures: int = MAX_FAILURES_PER_WINDOW,
        max_delay: float = MAX_DELAY_SECONDS,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_failures = max_failures
        self.max_delay = max_delay
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        stamps = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window_seconds]
        if stamps:
            self._failures[key] = stamps
        else:
            self._failures.pop(key, None)
        return stamps

    def blocked_for(self, key: str, *, now: float | None = None) -> float:
        """Seconds the caller has to wait before trying again, or ``0``."""
        current = time.time() if now is None else now
        stamps = self._recent(key, current)
        if len(stamps) < self.max_failures:
            return 0.0
        return max(0.0, self.window_seconds - (current - stamps[0]))

    def delay_for(self, key: str, *, now: float | None = None) -> float:
        """How long a rejected attempt takes to answer: it grows with the count."""
        current = time.time() if now is None else now
        count = len(self._recent(key, current))
        return min(self.max_delay, 0.25 * count)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        self._failures.setdefault(key, []).append(current)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


def client_key(scope: Scope) -> str:
    """A stable name for the caller: the address, since there is no proxy trust here."""
    client = scope.get('client')
    if client:
        return str(client[0])
    headers = Headers(scope=scope)
    return headers.get('x-forwarded-for', 'unknown').split(',')[0].strip()


def _is_free(path: str) -> bool:
    return path in FREE_PATHS or path.startswith(FREE_PREFIXES)


async def _deny(
    send: Send, *, status: int = 401, detail: str = 'unauthenticated', headers: dict[str, str] | None = None
) -> None:
    body = json.dumps({'error': detail}).encode('utf-8')
    payload: list[tuple[bytes, bytes]] = [
        (b'content-type', b'application/json; charset=utf-8'),
        (b'content-length', str(len(body)).encode()),
        (b'cache-control', b'no-store'),
    ]
    for name, value in (headers or {}).items():
        payload.append((name.encode(), value.encode()))
    await send({'type': 'http.response.start', 'status': status, 'headers': payload})
    await send({'type': 'http.response.body', 'body': body})


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


def session_cookie(auth: Auth, token: str, *, secure: bool = False) -> str:
    """The `Set-Cookie` value for a fresh session.

    `SameSite=Strict` and `HttpOnly` are what make the cookie unusable from a
    third-party page or from script; `Secure` is only added when the deployment
    terminates TLS in front of us, because adding it on plain HTTP drops the
    cookie and the login silently never sticks.
    """
    parts = [
        f'{auth.cookie_name}={token}',
        'Path=/',
        f'Max-Age={auth.ttl_seconds}',
        'HttpOnly',
        'SameSite=Strict',
    ]
    if secure:
        parts.append('Secure')
    return '; '.join(parts)


def cleared_cookie(auth: Auth) -> str:
    """The `Set-Cookie` value that logs out."""
    return f'{auth.cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'


__all__ = [
    'COOKIE_NAME',
    'FREE_PATHS',
    'SESSION_TTL_SECONDS',
    'Auth',
    'AuthMiddleware',
    'LoginThrottle',
    'cleared_cookie',
    'client_key',
    'session_cookie',
]
