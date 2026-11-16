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
from starlette.types import Scope, Send

COOKIE_NAME = 'sigil_session'
"""Name of the cookie the browser keeps. No `__Host-` prefix: TLS is the deployment's."""

BEARER_SCHEME = 'bearer'
"""The scheme a local client uses for the per-run token it read from the endpoint file.

Two credentials reach the same gate: the browser keeps a cookie, a terminal reads
a token from a ``0600`` file. The token is per run and is not a password — it
opens the API for the life of the process and nothing else.
"""

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
        service_token: str | None = None,
    ) -> None:
        self._password = password
        self.cookie_name = cookie_name
        self.ttl_seconds = ttl_seconds
        # Per process on purpose: no file to leak, and a restart revokes every session.
        self._key = signing_key if signing_key is not None else secrets.token_bytes(32)
        # Also per process, and also gone on restart: the token in the endpoint file
        # is the credential of one run of this service, not of a user.
        self._service_token = service_token

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

    def bearer_token(self, scope: Scope) -> str | None:
        """The per-run service token presented in ``Authorization: Bearer``, if any.

        Only the bearer scheme is read: an ``Authorization`` header carrying
        something else is not this credential and is left for whatever else might
        want it. The value is never logged or echoed — a refusal says
        ``unauthenticated`` and nothing more.
        """
        raw = Headers(scope=scope).get('authorization')
        if not raw:
            return None
        scheme, _, value = raw.partition(' ')
        if scheme.strip().lower() != BEARER_SCHEME:
            return None
        presented = value.strip()
        return presented or None

    def check_service_token(self, presented: object) -> bool:
        """Constant-time comparison against the token this run published."""

        if not isinstance(presented, str) or not presented or not self._service_token:
            return False
        return hmac.compare_digest(_digest(presented), _digest(self._service_token))

    def authenticated(self, scope: Scope) -> bool:
        """Whether the request carries either credential: the cookie or the token."""

        if self.verify(self.token_from(scope)):
            return True
        return self.check_service_token(self.bearer_token(scope))


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


__all__ = [
    'COOKIE_NAME',
    'FREE_PATHS',
    'SESSION_TTL_SECONDS',
    'Auth',
]
