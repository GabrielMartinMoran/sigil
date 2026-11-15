"""One model inside its account group."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.model_capability import ModelCapability


@dataclass(frozen=True, slots=True)
class GroupedModelChoice:
    account_id: str
    alias: str
    model: ModelCapability
    provider: str = ''

    @property
    def display_name(self) -> str:
        return f'{self.alias} · {self.provider}' if self.provider else self.alias
