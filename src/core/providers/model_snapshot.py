"""The ModelSnapshot value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.selection import Selection
from src.core.providers.domain.thinking import ThinkingChoice, ThinkingEffort


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    """Everything the UI shows about the active model."""

    account: ProviderAccount | None = None
    selection: Selection | None = None
    capability: ModelCapability | None = None

    window: int | None = None
    """Context window, ready for the meter."""

    efforts: tuple[ThinkingEffort, ...] = ()
    """Levels the active model accepts, for the effort menu."""

    images: bool = True
    """Whether the active model reads images, so the composer can refuse a paste
    before the turn is spent instead of failing at the provider."""

    notice: str | None = None
    """Why something is degraded (catalogue offline, model unknown)."""

    @property
    def is_configured(self) -> bool:
        return self.account is not None and self.selection is not None

    @property
    def alias(self) -> str:
        return self.account.alias if self.account is not None else ''

    @property
    def model(self) -> str:
        return self.selection.model if self.selection is not None else ''

    @property
    def thinking(self) -> ThinkingChoice:
        return self.selection.thinking if self.selection is not None else 'default'

    @property
    def label(self) -> str:
        """``"alias:model"``, the one string the debug report uses."""
        if not self.is_configured:
            return 'no model'
        return f'{self.alias}:{self.model}'

    @property
    def display_name(self) -> str:
        """``"openai/gpt-5 · work · OpenRouter"``: the model, and what serves it.

        The alias alone says nothing about who answers the request: two accounts of
        the same provider are told apart only by the alias, and an alias gives no clue
        about the vendor. A notice that announces the change carries both.
        """
        if not self.is_configured or self.account is None:
            return 'no model'
        return f'{self.model} · {self.account.display_name}'

    @property
    def short_label(self) -> str:
        """Alias only, for a narrow side panel."""
        return self.alias or 'no provider'
