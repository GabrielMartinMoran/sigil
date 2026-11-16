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

The pydantic-ai types this adapter deals with live in
``pydantic_ai.providers.openai_chatgpt``, which imports the OpenAI SDK — about a
third of a second of the cold start, paid on every start for code that only runs
when a ChatGPT account is built. Conformance there is structural (the SDK says so),
so this class does not need to subclass the protocol, and the import can wait until
a credential is actually read or written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.providers.domain.o_auth_secret import OAuthSecret
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.factory.oauth_provider_factory import OAuthProviderFactory
from src.core.providers.login.stored_credential_source import StoredCredentialSource, _sdk
from src.core.providers.store.secret_store import SecretStore

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_chatgpt import (
        OpenAIChatGPTProvider,
    )


def chatgpt_provider_factory(store: SecretStore) -> OAuthProviderFactory:
    """Builds the factory the builder registry expects.

    The factory closes over the credential store, so the builder stays free of it
    and a test can pass a fake store.
    """

    def factory(credential: OAuthSecret, account: ProviderAccount) -> OpenAIChatGPTProvider:
        return _sdk().OpenAIChatGPTProvider(
            credential_source=StoredCredentialSource(store=store, account=account, fallback=credential)
        )

    return factory


__all__ = ['StoredCredentialSource', 'chatgpt_provider_factory']
