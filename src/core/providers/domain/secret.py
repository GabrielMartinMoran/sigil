"""Credential material, in a shape that cannot leak by accident.

Two rules, both enforced by the type rather than by discipline:

1. ``repr`` and ``str`` never contain the secret. A traceback, a log line or a
   ``f'{credential}'`` shows ``ApiKeySecret(alias=work)``.
2. A builder receives the *narrow* variant it needs, so it cannot read a field
   that belongs to another auth method.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ApiKeySecret:
    """A pasted API key."""

    key: str = field(repr=False)
    alias: str = ''

    def __repr__(self) -> str:
        return f'ApiKeySecret(alias={self.alias!r})'

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class OAuthSecret:
    """Tokens from a browser sign-in, plus what is needed to refresh them.

    ``client_id`` and ``scope`` are not secrets: they identify the OAuth client
    registration and the granted permissions, and they are exactly what a
    support request needs. The three tokens are hidden.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    client_id: str = ''
    id_token: str | None = field(default=None, repr=False)
    scope: str = ''
    alias: str = ''

    @property
    def plan_usage_granted(self) -> bool:
        """Whether the account approved using its subscription quota."""
        return 'chatgpt.tokens.use.direct' in self.scope.split()

    def __repr__(self) -> str:
        return f'OAuthSecret(alias={self.alias!r}, client_id={self.client_id!r}, scope={self.scope!r})'

    __str__ = __repr__


SecretMaterial = ApiKeySecret | OAuthSecret
"""What the store hands to a builder."""
