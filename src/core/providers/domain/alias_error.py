"""The AliasError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.provider_error import ProviderError


class AliasError(ProviderError):
    """The alias is empty, malformed or already taken."""
