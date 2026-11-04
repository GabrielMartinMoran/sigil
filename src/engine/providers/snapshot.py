"""What the UI needs to paint the model state, in one immutable object.

The side panel gets this, never the service or the stores. That keeps the
widgets free of side effects and makes them trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.selection import Selection
from src.engine.providers.domain.thinking import ThinkingChoice, ThinkingEffort


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
    def short_label(self) -> str:
        """Alias only, for a narrow side panel."""
        return self.alias or 'no provider'


EMPTY_SNAPSHOT = ModelSnapshot()
"""What the app shows before anything is configured."""


@dataclass(frozen=True, slots=True)
class AccountView:
    """A row of the account list."""

    account: ProviderAccount
    active: bool = False
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def marker(self) -> str:
        return '●' if self.active else '○'
