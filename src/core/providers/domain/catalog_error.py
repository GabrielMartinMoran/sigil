"""The CatalogError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class CatalogError(ProviderError):
    """The model list could not be obtained and there is no fallback."""
