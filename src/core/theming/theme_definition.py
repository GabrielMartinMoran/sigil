"""The ThemeDefinition value of the core layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.theme import Theme

ROLES = (
    'primary',
    'secondary',
    'accent',
    'warning',
    'error',
    'success',
    'foreground',
    'background',
    'surface',
    'panel',
    'boost',
)


DEFAULT_DARK = True


@dataclass(frozen=True, slots=True)
class ThemeDefinition:
    """A theme as it was written, before Textual sees it."""

    name: str
    colors: dict[str, str]
    dark: bool = DEFAULT_DARK
    source: Path | None = None
    """File it came from. ``None`` for the builtin ones."""

    @property
    def builtin(self) -> bool:
        return self.source is None

    def role(self, name: str, fallback: str = '') -> str:
        """A palette color, or ``fallback`` when the theme does not define it."""
        return self.colors.get(name, fallback)

    def to_textual(self) -> Theme:
        """The Textual theme this definition describes.

        The roles go to their own fields and everything else to ``variables``, so
        `$tool-border` in the stylesheet resolves to the color the theme author
        wrote, and a role the theme omits keeps Textual's own derivation.
        """
        roles = {name: self.colors[name] for name in ROLES if name in self.colors}
        variables = {name: value for name, value in self.colors.items() if name not in ROLES}
        return Theme(name=self.name, dark=self.dark, variables=variables, **roles)
