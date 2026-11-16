"""A configured provider account.

The account is the unit of configuration: one credential, one alias, one wire
dialect. Several accounts of the same kind can coexist (two OpenRouter keys, a
personal and a work OpenAI org), which is why the identity is an opaque ``id``
and the ``alias`` is only a human label the user is free to change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.normalize_alias import normalize_alias
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.provider_now_iso import now_iso
from src.core.providers.domain.provider_variant import ProviderVariant

"""Letters, digits, space and a few separators; starts with a letter or digit."""


@dataclass(frozen=True, slots=True)
class ProviderAccount:
    """One credential plus how to use it."""

    id: str
    alias: str
    kind: ProviderKind
    variant: ProviderVariant = ProviderVariant.DEFAULT
    auth: AuthMethod = AuthMethod.API_KEY
    created_at: str = field(default_factory=now_iso)
    last_model: str | None = None

    @classmethod
    def create(
        cls,
        alias: str,
        kind: ProviderKind,
        variant: ProviderVariant = ProviderVariant.DEFAULT,
        auth: AuthMethod = AuthMethod.API_KEY,
    ) -> ProviderAccount:
        return cls(
            id=uuid.uuid4().hex,
            alias=normalize_alias(alias),
            kind=kind,
            variant=variant,
            auth=auth,
        )

    @property
    def matches_alias(self) -> str:
        """Lowercased alias, for comparisons. Aliases are unique ignoring case."""
        return self.alias.lower()

    @property
    def display_name(self) -> str:
        """``"alias · OpenCode Go"`` — what lists show."""
        return f'{self.alias} · {self.provider_label}'

    @property
    def provider_label(self) -> str:
        """``"OpenCode Go"`` or ``"OpenRouter"``."""
        kind = self.kind.label
        variant = self.variant.label
        return f'{kind} {variant}'.strip()

    @property
    def auth_label(self) -> str:
        return self.auth.label

    def with_model(self, model: str | None) -> ProviderAccount:
        """Copy pointing at ``model`` as the account's last choice."""
        return ProviderAccount(
            id=self.id,
            alias=self.alias,
            kind=self.kind,
            variant=self.variant,
            auth=self.auth,
            created_at=self.created_at,
            last_model=model,
        )

    def renamed(self, alias: str) -> ProviderAccount:
        return ProviderAccount(
            id=self.id,
            alias=normalize_alias(alias),
            kind=self.kind,
            variant=self.variant,
            auth=self.auth,
            created_at=self.created_at,
            last_model=self.last_model,
        )

    def to_dict(self) -> dict[str, object]:
        """Serializable form. It never contains a secret."""
        return {
            'id': self.id,
            'alias': self.alias,
            'kind': str(self.kind),
            'variant': str(self.variant),
            'auth': str(self.auth),
            'created_at': self.created_at,
            'last_model': self.last_model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ProviderAccount:
        """Rebuilds an account, tolerating unknown or missing optional fields.

        Raises:
            AliasingValueError: Never — the caller filters corrupt entries by
                catching ``ValueError``/``KeyError``.
        """
        return cls(
            id=str(data['id']),
            alias=str(data['alias']),
            kind=ProviderKind(str(data['kind'])),
            variant=ProviderVariant(str(data.get('variant') or ProviderVariant.DEFAULT)),
            auth=AuthMethod(str(data.get('auth') or AuthMethod.API_KEY)),
            created_at=str(data.get('created_at') or now_iso()),
            last_model=str(data['last_model']) if data.get('last_model') else None,
        )
