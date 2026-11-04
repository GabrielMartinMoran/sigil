"""Product variants inside a provider family.

OpenCode sells two products with the same account system but different
endpoints and very different catalogues: *Zen* (pay as you go, public model
list) and *Go* (subscription, no model list endpoint). Every other family has a
single variant, so ``DEFAULT`` is the absence of a choice rather than a real
alternative.
"""

from __future__ import annotations

from enum import StrEnum


class ProviderVariant(StrEnum):
    DEFAULT = 'default'
    ZEN = 'zen'
    GO = 'go'

    @property
    def label(self) -> str:
        return VARIANT_LABELS[self]


VARIANT_LABELS: dict[ProviderVariant, str] = {
    ProviderVariant.DEFAULT: '',
    ProviderVariant.ZEN: 'Zen',
    ProviderVariant.GO: 'Go',
}


def variants_for(kind: str) -> tuple[ProviderVariant, ...]:
    """Variants the UI offers for ``kind``."""
    from src.engine.providers.domain.provider_kind import ProviderKind

    if kind == ProviderKind.OPENCODE:
        return (ProviderVariant.ZEN, ProviderVariant.GO)
    return (ProviderVariant.DEFAULT,)
