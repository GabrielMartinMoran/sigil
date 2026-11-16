"""The provider families Sigil can talk to.

A *kind* names the service; the wire dialect and the credentials are separate
concerns (see ``ProviderVariant`` and ``AuthMethod``). Adding a family means a
new member here, a builder, a catalogue source and an entry in ``KIND_LABELS``.
"""

from __future__ import annotations

from enum import StrEnum


class ProviderKind(StrEnum):
    """A provider family."""

    OPENROUTER = 'openrouter'
    OPENCODE = 'opencode'
    OPENAI = 'openai'

    @property
    def label(self) -> str:
        """Name shown in the UI."""
        return KIND_LABELS[self]


KIND_LABELS: dict[ProviderKind, str] = {
    ProviderKind.OPENROUTER: 'OpenRouter',
    ProviderKind.OPENCODE: 'OpenCode',
    ProviderKind.OPENAI: 'OpenAI',
}
