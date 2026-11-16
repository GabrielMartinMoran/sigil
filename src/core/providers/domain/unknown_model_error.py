"""The UnknownModelError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class UnknownModelError(ProviderError):
    """The requested model is not in the catalogue and the user forced it."""
