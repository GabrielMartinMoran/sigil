"""How an account authenticates."""

from __future__ import annotations

from enum import StrEnum


class AuthMethod(StrEnum):
    API_KEY = 'api_key'
    OAUTH = 'oauth'

    @property
    def label(self) -> str:
        return AUTH_LABELS[self]


AUTH_LABELS: dict[AuthMethod, str] = {
    AuthMethod.API_KEY: 'API key',
    AuthMethod.OAUTH: 'Browser sign-in',
}
