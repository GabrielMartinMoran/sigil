"""The BuilderRegistry value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.o_auth_secret import OAuthSecret
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_error import ProviderError
from src.core.providers.factory.model_builder import ModelBuilder


class BuilderRegistry:
    """Resolves the builder that can serve an account."""

    def __init__(self, builders: tuple[ModelBuilder, ...]) -> None:
        self._builders = builders

    def for_account(self, account: ProviderAccount, secret: object) -> ModelBuilder:
        """The builder able to serve ``account`` with ``secret``.

        The credential decides between two builders of the same family, so an
        account whose stored secret was replaced stops mismatching its configured
        auth method silently.

        Raises:
            ProviderError: When no builder handles the pair.
        """
        wanted = AuthMethod.OAUTH if isinstance(secret, OAuthSecret) else AuthMethod.API_KEY
        for builder in self._builders:
            if builder.supports(account) and builder.auth is wanted:
                return builder
        raise ProviderError(
            f"There is no builder for an '{account.provider_label}' account using {wanted.label.lower()}."
        )

    @property
    def builders(self) -> tuple[ModelBuilder, ...]:
        return self._builders
