"""The LoginError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class LoginError(ProviderError):
    """A browser sign-in failed or was cancelled."""
