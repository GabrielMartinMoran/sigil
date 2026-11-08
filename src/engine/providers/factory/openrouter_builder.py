"""OpenRouter accounts.

One dialect, one endpoint, one key. The only nuance is that OpenRouter's own
provider is preferred over a raw OpenAI client: it carries the profile that knows
about cache breakpoints and route selection, which is what makes prompt caching
work on that service.
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.profiles import ModelProfile

from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.secret import SecretMaterial
from src.engine.providers.factory.builder import SIGIL_VERSION, require_api_key


class OpenRouterBuilder:
    """``OpenRouterModel`` over ``OpenRouterProvider``."""

    auth = AuthMethod.API_KEY

    def supports(self, account: ProviderAccount) -> bool:
        return account.kind is ProviderKind.OPENROUTER

    def build(
        self,
        account: ProviderAccount,
        model: str,
        capability: ModelCapability,
        secret: SecretMaterial,
    ) -> Model:
        key = require_api_key(account, secret)
        # Imported here: the OpenRouter classes pull in the OpenAI SDK stack, which
        # is a large share of the cold start, and nothing on the first paint builds
        # a model — the stored selection already carries the one it needs.
        from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        provider = OpenRouterProvider(api_key=key.key, app_title=f'Sigil {SIGIL_VERSION}')
        settings: OpenRouterModelSettings | None = None
        if model.startswith('anthropic/'):
            settings = {
                'openrouter_cache_instructions': True,
                'openrouter_cache_tool_definitions': True,
                'openrouter_cache_messages': True,
            }
        return OpenRouterModel(model_name=model, provider=provider, profile=_profile(capability), settings=settings)


def _profile(capability: ModelCapability) -> ModelProfile:
    """Pins the window so the panel matches the catalogue, not a guess.

    OpenRouter reports efforts per model but pydantic-ai maps the unified
    ``thinking`` value itself, so only the window needs to travel.
    """
    profile: ModelProfile = {}
    if capability.context_window:
        profile['context_window'] = capability.context_window
    if capability.efforts:
        profile['supports_thinking'] = True
    return profile
