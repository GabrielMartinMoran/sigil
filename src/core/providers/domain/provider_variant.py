"""Which provider implementation a variant uses."""

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
