"""Where the credentials live.

``FileSecretStore`` keeps them in a single ``0600`` JSON file keyed by account id.
The ``SecretStore`` protocol exists because the storage is the part most likely
to change (a keyring backend, an encrypted file), and the rest of the code should
not care which one it gets.
"""

from __future__ import annotations

from typing import Protocol

from src.core.providers.domain.secret import SecretMaterial


class SecretStore(Protocol):
    """Persistence of credential material, keyed by account id."""

    def get(self, account_id: str) -> SecretMaterial | None:
        """The stored credential, or ``None`` when it is missing."""
        ...

    def put(self, account_id: str, secret: SecretMaterial) -> None:
        """Stores (or replaces) the credential."""
        ...

    def delete(self, account_id: str) -> None:
        """Forgets the credential. Missing ids are not an error."""
        ...
