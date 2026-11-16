"""The NoAccountError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class NoAccountError(ProviderError):
    """The action needs a configured account and there is none."""

    def __init__(self) -> None:
        super().__init__('No provider is configured yet. Run /provider to add one.')
