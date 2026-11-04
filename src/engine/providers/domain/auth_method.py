"""How an account proves who it is.

``API_KEY`` is pasted by the user. ``OAUTH`` comes out of a browser sign-in and
carries refreshable tokens (the ChatGPT plan case). The distinction drives the
form the UI shows and the builder the factory picks, so it is a domain concept,
not a transport detail.
"""

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


def auth_methods_for(kind: str) -> tuple[AuthMethod, ...]:
    """Auth methods the UI offers for ``kind``."""
    from src.engine.providers.domain.provider_kind import ProviderKind

    if kind == ProviderKind.OPENAI:
        return (AuthMethod.API_KEY, AuthMethod.OAUTH)
    return (AuthMethod.API_KEY,)
