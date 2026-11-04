"""Errors the provider layer raises.

They all carry a message written for a person: the UI shows them verbatim in the
chat, so no stack trace and no secret ever reaches the user through this path.
The exception types exist for the code that has to react differently, not to be
printed.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base of every provider failure with a user-facing message."""


class AliasError(ProviderError):
    """The alias is empty, malformed or already taken."""


class AccountNotFound(ProviderError):
    """No account with that alias."""

    def __init__(self, alias: str) -> None:
        super().__init__(f"There is no provider account called '{alias}'.")
        self.alias = alias


class LastAccountError(ProviderError):
    """Removing this account would leave Sigil with no model at all."""

    def __init__(self) -> None:
        super().__init__('This is the only provider account. Add another one before removing it.')


class NoAccountError(ProviderError):
    """The action needs a configured account and there is none."""

    def __init__(self) -> None:
        super().__init__('No provider is configured yet. Run /provider to add one.')


class SecretRejectedError(ProviderError):
    """The provider answered that the credential is not valid."""

    def __init__(self, detail: str = '') -> None:
        message = 'The provider rejected that credential.'
        super().__init__(f'{message} {detail}'.strip())


class CredentialError(ProviderError):
    """A stored credential is missing or cannot be used."""


class CatalogError(ProviderError):
    """The model list could not be obtained and there is no fallback."""


class UnknownModelError(ProviderError):
    """The requested model is not in the catalogue and the user forced it."""


class LoginError(ProviderError):
    """A browser sign-in failed or was cancelled."""


class BusyError(ProviderError):
    """A turn is running, so the model cannot be swapped right now."""

    def __init__(self) -> None:
        super().__init__('A turn is in progress. Wait for it to finish (or stop it) before changing the model.')
