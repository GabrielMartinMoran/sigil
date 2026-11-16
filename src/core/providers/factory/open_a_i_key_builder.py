"""The OpenAIKeyBuilder value of the providers layer."""

from __future__ import annotations

from pydantic_ai.models import Model

from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.secret import SecretMaterial
from src.core.providers.factory.builder import require_api_key


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
