"""The account a form is creating."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.provider_variant import ProviderVariant


@dataclass(frozen=True, slots=True)
class NewAccount:
    """What the form collected."""

    kind: ProviderKind
    variant: ProviderVariant
    auth: AuthMethod
    alias: str
    api_key: str = ''
    """Empty when ``auth`` is ``OAUTH``."""
