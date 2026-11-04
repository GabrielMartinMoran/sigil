"""OpenCode accounts, in both variants and all three dialects.

The hard part of this provider is that it is not one API. The catalogue says, per
model, which dialect serves it and on which base URL:

- ``chat-completions`` and ``responses`` → OpenAI SDK, different model class.
  Built through ``OpenAICompatibleProvider`` rather than ``OpenAIProvider``: the
  latter declares itself to be the OpenAI API, and pydantic-ai would then ship
  OpenAI's encrypted reasoning to a service that does not understand it.
- ``messages`` → Anthropic SDK. Requires the optional extra; without it the
  builder raises ``DialectUnsupportedError`` and the caller can explain.

Go (the subscription) has no catalogue endpoint of its own, so its base URL is
fixed here. It also rejects any request that does not carry
``x-opencode-session``, which is why the headers go on the client and the session
id is a constructor argument rather than a constant.
"""

from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant
from src.engine.providers.domain.secret import SecretMaterial
from src.engine.providers.factory.builder import (
    SIGIL_VERSION,
    DialectUnsupportedError,
    require_api_key,
)

GO_BASE_URL = 'https://opencode.ai/inference/go/openai/v1'
ZEN_OPENAI_BASE_URL = 'https://opencode.ai/inference/openai/v1'
ZEN_ANTHROPIC_BASE_URL = 'https://opencode.ai/inference/anthropic/v1'

PROVIDER_NAME = 'opencode'
"""System name pydantic-ai records in the history.

It must stay stable: stateful artifacts only travel back to a provider with the
same name.
"""

MESSAGES_DIALECT = 'messages'
RESPONSES_DIALECT = 'responses'
CHAT_DIALECT = 'chat-completions'


def opencode_headers(session_id: str) -> dict[str, str]:
    """Headers OpenCode expects from a third-party client."""
    return {'User-Agent': f'sigil/{SIGIL_VERSION}', 'x-opencode-session': session_id}


class OpenCodeBuilder:
    """Builds the model class the catalogue's dialect asks for."""

    auth = AuthMethod.API_KEY

    def __init__(self, session_id: str) -> None:
        """
        Args:
            session_id: Stable id of the conversation. It is sent on every request
                so OpenCode can route and cache consistently, and Go rejects the
                request without it.
        """
        self._session_id = session_id

    def supports(self, account: ProviderAccount) -> bool:
        return account.kind is ProviderKind.OPENCODE

    def build(
        self,
        account: ProviderAccount,
        model: str,
        capability: ModelCapability,
        secret: SecretMaterial,
    ) -> Model:
        key = require_api_key(account, secret)
        if capability.dialect == MESSAGES_DIALECT:
            return self._build_messages(model, capability, key.key)
        return self._build_openai(account, model, capability, key.key)

    # --- internals ---------------------------------------------------------

    def _build_openai(
        self,
        account: ProviderAccount,
        model: str,
        capability: ModelCapability,
        api_key: str,
    ) -> Model:
        # The client is built here rather than by the provider because OpenCode's
        # headers must be on the client: `AsyncOpenAI.default_headers` is exposed
        # as a read-only copy, so mutating it after construction has no effect.
        client = AsyncOpenAI(
            base_url=self._base_url(account, capability),
            api_key=api_key,
            default_headers=opencode_headers(self._session_id),
        )
        provider = OpenAICompatibleProvider(
            PROVIDER_NAME,
            openai_client=client,
            profile=_openai_profile(capability),
            canonical_system=PROVIDER_NAME,
        )

        if capability.dialect == RESPONSES_DIALECT:
            return OpenAIResponsesModel(model_name=model, provider=provider)
        return OpenAIChatModel(model_name=model, provider=provider)

    def _build_messages(self, model: str, capability: ModelCapability, api_key: str) -> Model:
        try:
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider
        except ImportError as exc:  # pragma: no cover - depends on an optional extra
            raise DialectUnsupportedError(
                'This model is served over the Anthropic API and that extra is not installed. '
                'Install it with `uv add "pydantic-ai-slim[anthropic]"`, or pick a model served '
                'over Chat Completions.'
            ) from exc

        provider = AnthropicProvider(api_key=api_key, base_url=capability.base_url or ZEN_ANTHROPIC_BASE_URL)
        return AnthropicModel(model_name=model, provider=provider, profile=_openai_profile(capability))

    def _base_url(self, account: ProviderAccount, capability: ModelCapability) -> str:
        if account.variant is ProviderVariant.GO:
            return GO_BASE_URL
        return capability.base_url or ZEN_OPENAI_BASE_URL


def _openai_profile(capability: ModelCapability) -> dict[str, object]:
    """What pydantic-ai needs to read reasoning the way this route sends it.

    ``openai_chat_thinking_field`` is essential: OpenCode returns reasoning in
    ``reasoning_content``, and without the field name that text arrives as an
    ordinary answer and the agent replays its own thoughts as output.
    """
    profile: dict[str, object] = {}
    if capability.context_window:
        profile['context_window'] = capability.context_window
    if capability.efforts:
        profile['supports_thinking'] = True
        profile['openai_reasoning_efforts'] = frozenset(capability.efforts)
        profile['openai_chat_thinking_field'] = 'reasoning_content'
    return profile
