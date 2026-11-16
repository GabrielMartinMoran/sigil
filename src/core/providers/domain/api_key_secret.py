"""The ApiKeySecret value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ApiKeySecret:
    """A pasted API key."""

    key: str = field(repr=False)
    alias: str = ''

    def __repr__(self) -> str:
        return f'ApiKeySecret(alias={self.alias!r})'

    __str__ = __repr__
