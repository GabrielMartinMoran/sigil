"""The CredentialError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class CredentialError(ProviderError):
    """A stored credential is missing or cannot be used."""
