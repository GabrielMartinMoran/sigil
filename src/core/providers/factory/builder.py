"""Building a pydantic-ai model from an account, a model id and its capability.

Each provider family gets a builder. A builder is the only place that knows both
the SDK types and the wire details (base URL, dialect, required headers), which
keeps the rest of the application free of them.

The capability is not decoration: the context window and the accepted reasoning
levels are injected into the model's profile, because pydantic-ai cannot know
them for a model it has never heard of, and the panel must not lie.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.providers.domain.api_key_secret import ApiKeySecret
from src.core.providers.domain.o_auth_secret import OAuthSecret
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.secret import SecretMaterial

if TYPE_CHECKING:
    from pydantic_ai.models import ModelSettings

SIGIL_VERSION = '0.1.0'
"""Sent as the user agent. OpenCode Go asks clients to identify themselves."""


def require_api_key(account: ProviderAccount, secret: SecretMaterial) -> ApiKeySecret:
    """Narrows ``secret``, or explains what went wrong.

    Raises:
        CredentialError: When the stored credential is not an API key.
    """
    from src.core.providers.domain.credential_error import CredentialError

    if not isinstance(secret, ApiKeySecret):
        raise CredentialError(
            f"The account '{account.alias}' is configured for a browser sign-in, "
            'but its stored credential is not an API key.'
        )
    return secret


def require_oauth(account: ProviderAccount, secret: SecretMaterial) -> OAuthSecret:
    """Narrows ``secret`` to an OAuth credential.

    Raises:
        CredentialError: When the stored credential is not an OAuth one.
    """
    from src.core.providers.domain.credential_error import CredentialError

    if not isinstance(secret, OAuthSecret):
        raise CredentialError(
            f"The account '{account.alias}' is configured with an API key, but this route needs a browser sign-in."
        )
    return secret


def client_settings() -> ModelSettings:
    """Settings every request shares. Kept as a function so it stays immutable."""
    return {}
