"""The variants_for helper of the providers layer."""

from __future__ import annotations

from src.core.providers.domain.provider_variant import ProviderVariant


def variants_for(kind: str) -> tuple[ProviderVariant, ...]:
    """Variants the UI offers for ``kind``."""
    from src.core.providers.domain.provider_kind import ProviderKind

    if kind == ProviderKind.OPENCODE:
        return (ProviderVariant.ZEN, ProviderVariant.GO)
    return (ProviderVariant.DEFAULT,)
