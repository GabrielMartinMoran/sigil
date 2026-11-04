"""``OAuthCredentialSource`` over the file-backed credential store.

pydantic-ai owns the token lifecycle for the ChatGPT plan: it checks expiry,
refreshes single-flight after a 401, and hands the rotated tokens back. What it
cannot do is decide where they live, which is exactly what this adapter answers.

Two details matter:

- ``load()`` re-reads the store before a refresh, because refresh tokens are
  single-use and another process using the same file may have rotated them
  already. That is best effort, not mutual exclusion.
- ``save()`` writes through ``OAuthSecret``, so the file keeps the same shape as
  the rest of the credentials and the tokens stay hidden from any ``repr``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.providers.openai_chatgpt import (
    OpenAIChatGPTCredentials,
    OpenAIChatGPTCredentialSource,
    OpenAIChatGPTProvider,
)

from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.secret import OAuthSecret
from src.engine.providers.store.secret_store import SecretStore


@dataclass(slots=True)
class StoredCredentialSource(OpenAIChatGPTCredentialSource):
    """Reads and writes the plan's credentials in the credential store."""

    store: SecretStore
    account: ProviderAccount
    fallback: OAuthSecret
    """What to use when the store has nothing yet (first run after sign-in)."""

    async def load(self) -> OpenAIChatGPTCredentials:
        current = self.store.get(self.account.id)
        source = current if isinstance(current, OAuthSecret) else self.fallback
        return OpenAIChatGPTCredentials(
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


def chatgpt_provider_factory(store: SecretStore):
    """Builds the factory the builder registry expects.

    The factory closes over the credential store, so the builder stays free of it
    and a test can pass a fake store.
    """

    def factory(credential: OAuthSecret, account: ProviderAccount) -> OpenAIChatGPTProvider:
        return OpenAIChatGPTProvider(
            credential_source=StoredCredentialSource(store=store, account=account, fallback=credential)
        )

    return factory
