"""The OAuthSecret value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass, field


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
