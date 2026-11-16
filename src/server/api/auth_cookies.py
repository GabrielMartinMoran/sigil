"""The cookies that carry a login."""

from __future__ import annotations

from src.server.api.auth import Auth


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
