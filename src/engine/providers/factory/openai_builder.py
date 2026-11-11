"""OpenAI accounts: API key and ChatGPT-plan sign-in.

The two auth methods end at the same place — ``OpenAIResponsesModel`` — but with
different providers:

- API key → ``OpenAIProvider``. Plain bearer token.
- OAuth → ``OpenAIChatGPTProvider``. It owns the token lifecycle (expiry check,
  refresh, single-flight after a 401) and persists rotated tokens through the
  credential source we hand it.

The Responses API is used in both cases because it carries reasoning natively,
which is what the agent needs to reason across turns.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.models import Model

from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.secret import OAuthSecret, SecretMaterial
from src.engine.providers.factory.builder import require_api_key, require_oauth

ChatGPTProviderFactory = Callable[[OAuthSecret, ProviderAccount], object]
"""Builds the OAuth-backed provider for an account.

Injected so this module does not import the credential store, and so a test can
substitute a provider that never talks to the network.
"""


class OpenAIKeyBuilder:
    """``OpenAIResponsesModel`` over ``OpenAIProvider``."""

    auth = AuthMethod.API_KEY

    def supports(self, account: ProviderAccount) -> bool:
        return account.kind is ProviderKind.OPENAI

    def build(
        self,
        account: ProviderAccount,
        model: str,
        capability: ModelCapability,
        secret: SecretMaterial,
    ) -> Model:
        # Imported here: `pydantic_ai.models.openai` pulls in the whole `openai`
        # SDK, ~300 ms of the cold start, and nothing on the first paint builds a
        # model — the selection already carries the one it needs.
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        key = require_api_key(account, secret)
        provider = OpenAIProvider(api_key=key.key)
        return OpenAIResponsesModel(
            model_name=model,
            provider=provider,
            profile=_profile(capability),
            settings={'openai_reasoning_summary': 'auto'} if capability.efforts else None,
        )


class OpenAIOAuthBuilder:
    """``OpenAIResponsesModel`` over the ChatGPT-plan provider."""

    auth = AuthMethod.OAUTH

    def __init__(self, provider_factory: ChatGPTProviderFactory) -> None:
        self._provider_factory = provider_factory

    def supports(self, account: ProviderAccount) -> bool:
        return account.kind is ProviderKind.OPENAI

    def build(
        self,
        account: ProviderAccount,
        model: str,
        capability: ModelCapability,
        secret: SecretMaterial,
    ) -> Model:
        from pydantic_ai.models.openai import OpenAIResponsesModel

        credential = require_oauth(account, secret)
        provider = self._provider_factory(credential, account)
        # Subscription sharing rejects configuration_update input items. Send the
        # selected effort at request level instead, even after a history change.
        profile = {**_profile(capability), 'supports_mid_conversation_effort': False}
        return OpenAIResponsesModel(
            model_name=model,
            provider=provider,
            profile=profile,
            settings={'openai_reasoning_summary': 'auto'} if capability.efforts else None,
        )


def _profile(capability: ModelCapability) -> dict[str, object]:
    """Window and efforts as the catalogue reported them.

    OpenAI's own bundled profiles already know the reasoning levels, so the
    catalogue value is a refinement, not the only source; it is injected because
    the bundled data lags behind a freshly released model.
    """
    profile: dict[str, object] = {}
    if capability.context_window:
        profile['context_window'] = capability.context_window
    if capability.efforts:
        profile['supports_thinking'] = True
        profile['openai_reasoning_efforts'] = frozenset(capability.efforts)
    return profile
