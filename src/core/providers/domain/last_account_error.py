"""The LastAccountError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class LastAccountError(ProviderError):
    """Removing this account would leave Sigil with no model at all."""

    def __init__(self) -> None:
        super().__init__('This is the only provider account. Add another one before removing it.')
