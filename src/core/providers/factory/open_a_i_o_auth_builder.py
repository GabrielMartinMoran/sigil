"""The OpenAIOAuthBuilder value of the providers layer."""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.models import Model

from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.o_auth_secret import OAuthSecret
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.secret import SecretMaterial
from src.core.providers.factory.builder import require_oauth
from src.core.providers.factory.open_a_i_key_builder import _profile

ChatGPTProviderFactory = Callable[[OAuthSecret, ProviderAccount], object]


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
