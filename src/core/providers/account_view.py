"""The AccountView value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.providers.domain.provider_account import ProviderAccount


@dataclass(frozen=True, slots=True)
class AccountView:
    """A row of the account list."""

    account: ProviderAccount
    active: bool = False
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def marker(self) -> str:
        return '●' if self.active else '○'
