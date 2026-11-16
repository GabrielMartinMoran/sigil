"""The LoginFlow value of the providers layer."""

from __future__ import annotations

from typing import Protocol

from src.core.providers.domain.o_auth_secret import OAuthSecret
from src.core.providers.domain.provider_account import ProviderAccount


class LoginFlow(Protocol):
    """A browser sign-in, as the service needs to see it."""

    async def sign_in(self, account: ProviderAccount) -> OAuthSecret:
        """Blocks until the browser hands back credentials.

        Raises:
            ProviderError: When the sign-in fails or is abandoned.
        """
