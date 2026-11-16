"""The BusyError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class BusyError(ProviderError):
    """A turn is running, so the model cannot be swapped right now."""

    def __init__(self) -> None:
        super().__init__('A turn is in progress. Wait for it to finish (or stop it) before changing the model.')
