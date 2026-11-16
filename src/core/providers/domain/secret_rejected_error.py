"""The SecretRejectedError value of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.alias_error import ProviderError


class SecretRejectedError(ProviderError):
    """The provider answered that the credential is not valid."""

    def __init__(self, detail: str = '') -> None:
        message = 'The provider rejected that credential.'
        super().__init__(f'{message} {detail}'.strip())
