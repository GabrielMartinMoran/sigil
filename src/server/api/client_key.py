"""A stable per-client key for throttling."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import Scope


def client_key(scope: Scope) -> str:
    """A stable name for the caller: the address, since there is no proxy trust here."""
    client = scope.get('client')
    if client:
        return str(client[0])
    headers = Headers(scope=scope)
    return headers.get('x-forwarded-for', 'unknown').split(',')[0].strip()
