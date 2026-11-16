"""The StoredCredentialSource value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING

from src.core.providers.domain.o_auth_secret import OAuthSecret
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.store.secret_store import SecretStore

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_chatgpt import (
        OpenAIChatGPTCredentials,
    )


def _sdk() -> ModuleType:
    """The pydantic-ai ChatGPT module, imported on first use."""
    from pydantic_ai.providers import openai_chatgpt

    return openai_chatgpt


@dataclass(slots=True)
class StoredCredentialSource:
    """Reads and writes the plan's credentials in the credential store.

    It satisfies ``OpenAIChatGPTCredentialSource`` structurally: that protocol asks
    for ``load`` and ``save`` with these signatures, and pydantic-ai accepts any
    object that provides them.
    """

    store: SecretStore
    account: ProviderAccount
    fallback: OAuthSecret
    """What to use when the store has nothing yet (first run after sign-in)."""

    async def load(self) -> OpenAIChatGPTCredentials:
        current = self.store.get(self.account.id)
        source = current if isinstance(current, OAuthSecret) else self.fallback
        return _sdk().OpenAIChatGPTCredentials(
            client_id=source.client_id or self.fallback.client_id,
            access_token=source.access_token,
            refresh_token=source.refresh_token,
            id_token=source.id_token,
            scope=source.scope,
        )

    async def save(self, credentials: OpenAIChatGPTCredentials) -> None:
        self.store.put(
            self.account.id,
            OAuthSecret(
                access_token=credentials.access_token,
                refresh_token=credentials.refresh_token,
                client_id=credentials.client_id,
                id_token=credentials.id_token,
                scope=credentials.scope,
                alias=self.account.alias,
            ),
        )
