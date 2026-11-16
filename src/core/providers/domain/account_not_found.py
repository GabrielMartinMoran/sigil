"""The AccountNotFound value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class AccountNotFound(ProviderError):
    """No account with that alias."""

    def __init__(self, alias: str) -> None:
        super().__init__(f"There is no provider account called '{alias}'.")
        self.alias = alias
