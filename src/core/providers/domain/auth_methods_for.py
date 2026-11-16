"""The auth_methods_for helper of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.auth_method import AuthMethod


def auth_methods_for(kind: str) -> tuple[AuthMethod, ...]:
    """Auth methods the UI offers for ``kind``."""
    from src.core.providers.domain.provider_kind import ProviderKind

    if kind == ProviderKind.OPENAI:
        return (AuthMethod.API_KEY, AuthMethod.OAUTH)
    return (AuthMethod.API_KEY,)
