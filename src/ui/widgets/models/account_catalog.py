"""The models one account offers."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.model_catalog import ModelCatalog


@dataclass(frozen=True, slots=True)
class AccountCatalog:
    account_id: str
    alias: str
    catalog: ModelCatalog
    provider: str = ''
    """What the account talks to — ``"OpenRouter"``, ``"OpenCode Go"``.

    The alias is a name the user chose and it is the only handle the service takes;
    the provider is what answers. A list that shows one without the other asks the
    reader to remember which is which.
    """

    @property
    def display_name(self) -> str:
        return f'{self.alias} · {self.provider}' if self.provider else self.alias
