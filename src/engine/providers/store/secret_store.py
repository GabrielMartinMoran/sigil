"""Where the credentials live.

``FileSecretStore`` keeps them in a single ``0600`` JSON file keyed by account id.
The ``SecretStore`` protocol exists because the storage is the part most likely
to change (a keyring backend, an encrypted file), and the rest of the code should
not care which one it gets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.engine.providers.domain.provider_error import CredentialError
from src.engine.providers.domain.secret import ApiKeySecret, OAuthSecret, SecretMaterial
from src.engine.providers.store.atomic import MODE_PRIVATE, read_json, tighten, write_json

API_KEY_TYPE = 'api_key'
OAUTH_TYPE = 'oauth'


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


class FileSecretStore:
    """Secrets in one private JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def get(self, account_id: str) -> SecretMaterial | None:
        entry = self._read().get(account_id)
        if not isinstance(entry, dict):
            return None
        try:
            return _decode(entry)
        except KeyError, TypeError, ValueError:
            return None

    def put(self, account_id: str, secret: SecretMaterial) -> None:
        data = self._read()
        data[account_id] = _encode(secret)
        self._write(data)

    def delete(self, account_id: str) -> None:
        data = self._read()
        if data.pop(account_id, None) is not None:
            self._write(data)

    # --- internals ---------------------------------------------------------

    def _read(self) -> dict[str, object]:
        # Tightening on read covers files written by an older version or
        # restored from a backup with the wrong mode.
        tighten(self._path, MODE_PRIVATE)
        data = read_json(self._path)
        return dict(data) if data else {}

    def _write(self, data: dict[str, object]) -> None:
        try:
            write_json(self._path, data, mode=MODE_PRIVATE)
        except OSError as exc:
            raise CredentialError(f'Could not save the credentials file: {exc}') from exc


def _encode(secret: SecretMaterial) -> dict[str, object]:
    if isinstance(secret, ApiKeySecret):
        return {'type': API_KEY_TYPE, 'key': secret.key}
    return {
        'type': OAUTH_TYPE,
        'access_token': secret.access_token,
        'refresh_token': secret.refresh_token,
        'client_id': secret.client_id,
        'id_token': secret.id_token,
        'scope': secret.scope,
    }


def _decode(entry: dict[str, object]) -> SecretMaterial:
    kind = entry.get('type')
    if kind == API_KEY_TYPE:
        key = entry.get('key')
        if not isinstance(key, str) or not key:
            raise ValueError('missing key')
        return ApiKeySecret(key=key)

    if kind == OAUTH_TYPE:
        access = entry.get('access_token')
        refresh = entry.get('refresh_token')
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise ValueError('missing token')
        client_id = entry.get('client_id')
        id_token = entry.get('id_token')
        scope = entry.get('scope')
        return OAuthSecret(
            access_token=access,
            refresh_token=refresh,
            client_id=client_id if isinstance(client_id, str) else '',
            id_token=id_token if isinstance(id_token, str) else None,
            scope=scope if isinstance(scope, str) else '',
        )

    raise ValueError(f'unknown credential type: {kind!r}')
