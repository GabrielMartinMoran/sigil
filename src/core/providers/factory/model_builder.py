"""The ModelBuilder value of the providers layer."""

from __future__ import annotations

from typing import Protocol

from pydantic_ai.models import Model

from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.secret import SecretMaterial


class ModelBuilder(Protocol):
    """Turns an account into a usable model."""

    auth: AuthMethod
    """Credential kind this builder consumes. Decides which builder an account
    gets when a family offers more than one (OpenAI key vs OAuth)."""

    def supports(self, account: ProviderAccount) -> bool:
        """Whether this builder handles ``account``'s kind and variant."""
        ...

    def build(
        self,
        account: ProviderAccount,
        model: str,
        capability: ModelCapability,
        secret: SecretMaterial,
    ) -> Model:
        """Builds the model.

        Args:
            account: The account whose credential is being used.
            model: Model id to send to the API.
            capability: What the catalogue said about that model.
            secret: The credential, already narrowed to the right auth method.

        Raises:
            CredentialError: When the secret kind does not match the account.
        """
        ...
