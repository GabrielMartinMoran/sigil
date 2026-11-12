"""Themes as data: JSON files under ``~/.sigil/themes``.

A theme is a set of named colors, nothing else. Keeping it out of Python means a
user can install one by dropping a file in a directory, and the builtin ones are
the same kind of object as the ones a user writes — there is no privileged path.

The file shape is deliberately flat and small::

    {
      "name": "synthwave",
      "dark": true,
      "colors": {
        "primary": "#ff2a6d",
        "accent": "#05d9e8",
        "background": "#0d0221",
        "foreground": "#f8f8f2"
      }
    }

Two kinds of key live under ``colors``:

- the roles Textual knows (``primary``, ``secondary``, ``accent``, ``warning``,
  ``error``, ``success``, ``foreground``, ``background``, ``surface``, ``panel``,
  ``boost``), which become the theme's own palette;
- anything else, which becomes a CSS variable of that name. That is how a theme
  reaches the parts of Sigil the palette does not cover: the tool call border, the
  reasoning marker, the diff counters.

A color that does not parse rejects the file instead of half-applying it: a theme
with two valid colors and one typo would paint an interface nobody designed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from textual.color import Color, ColorParseError
from textual.theme import Theme

from src.engine.paths import themes_dir

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
"""Color keys that become the theme palette instead of CSS variables."""

SCHEMA_VERSION = 1
DEFAULT_DARK = True


class ThemeError(ValueError):
    """A theme file that cannot be used, with the reason in the message."""


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


def _validated(data: object, *, source: Path | None) -> ThemeDefinition:
    """Reads a theme out of parsed JSON, or raises ``ThemeError``."""
    where = f' ({source})' if source is not None else ''
    if not isinstance(data, dict):
        raise ThemeError(f'A theme must be a JSON object{where}.')

    version = data.get('version', SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ThemeError(f'Unsupported theme version {version!r}{where}; expected {SCHEMA_VERSION}.')

    name = data.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ThemeError(f'A theme needs a name{where}.')

    colors = data.get('colors')
    if not isinstance(colors, dict) or not colors:
        raise ThemeError(f'Theme {name!r} has no colors{where}.')
    if 'primary' not in colors:
        raise ThemeError(f'Theme {name!r} needs a primary color{where}.')

    parsed: dict[str, str] = {}
    for key, value in colors.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ThemeError(f'Theme {name!r} has a color that is not a string{where}.')
        try:
            Color.parse(value)
        except ColorParseError as exc:
            raise ThemeError(f'Theme {name!r}: {key}={value!r} is not a color{where}.') from exc
        parsed[key] = value

    dark = data.get('dark', DEFAULT_DARK)
    if not isinstance(dark, bool):
        raise ThemeError(f'Theme {name!r}: "dark" must be true or false{where}.')

    return ThemeDefinition(name=name.strip(), colors=parsed, dark=dark, source=source)


def load_file(path: Path) -> ThemeDefinition:
    """Reads one theme file. Raises ``ThemeError`` with the reason."""
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise ThemeError(f'Cannot read {path}: {exc}') from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ThemeError(f'{path} is not valid JSON: {exc}') from exc
    return _validated(data, source=path)


def _sigil() -> ThemeDefinition:
    """Sigil's ember palette, adapted for legible terminal surfaces."""
    return ThemeDefinition(
        name='sigil',
        dark=True,
        colors={
            'logo-start': '#ff9d00',
            'logo-end': '#d93d00',
            'primary': '#f59e0b',
            'secondary': '#d55a1f',
            'accent': '#f59e0b',
            'warning': '#f5b64c',
            'error': '#ef7168',
            'success': '#9ebd83',
            'foreground': '#e7d7c1',
            'background': '#0b0a0d',
            'surface': '#17100f',
            'panel': '#211512',
            'boost': '#352018',
            'tool-border': '#704431',
            'tool-title': '#f59e0b',
            'tool-output': '#bcaa99',
            'reasoning': '#bcaa99',
            'diff-added': '#9ebd83',
            'diff-removed': '#ef7168',
            'sidebar-section': '#d6b99d',
            'muted': '#ad9988',
        },
    )


def _dark() -> ThemeDefinition:
    """A neutral dark surface with a cool accent.

    Deliberately quiet. It should not
    compete with the content, and it must stay legible on a black terminal where
    pure black would erase the panel borders.
    """
    return ThemeDefinition(
        name='dark',
        dark=True,
        colors={
            'primary': '#4a9eff',
            'secondary': '#2a6fa8',
            'accent': '#f0b429',
            'warning': '#e0a030',
            'error': '#e5484d',
            'success': '#46a758',
            'foreground': '#d4d4d8',
            'background': '#121216',
            'surface': '#1b1b21',
            'panel': '#24242c',
            'boost': '#32323d',
            'tool-border': '#3a3a46',
            'tool-title': '#8f8fa3',
            'tool-output': '#6f6f80',
            'reasoning': '#7a7a8c',
            'diff-added': '#46a758',
            'diff-removed': '#e5484d',
            'sidebar-section': '#8f8fa3',
            'muted': '#6f6f80',
        },
    )


def _synthwave() -> ThemeDefinition:
    """Neon on deep purple: the opposite end of the palette on purpose.

    The point of a second builtin is to prove the theme system can move every
    surface, not just the accent. The hues come from the sunset-and-grid imagery
    the name refers to: hot pink, electric cyan, and a violet that reads as the
    sky behind them.
    """
    return ThemeDefinition(
        name='synthwave',
        dark=True,
        colors={
            'primary': '#ff2a6d',
            'secondary': '#b967ff',
            'accent': '#05d9e8',
            'warning': '#f9c80e',
            'error': '#ff3864',
            'success': '#01ffc3',
            'foreground': '#f8f8f2',
            'background': '#0d0221',
            'surface': '#1a0b2e',
            'panel': '#241436',
            'boost': '#33204d',
            'tool-border': '#4b2a6b',
            'tool-title': '#b967ff',
            'tool-output': '#8a7ba8',
            'reasoning': '#9d8ec7',
            'diff-added': '#01ffc3',
            'diff-removed': '#ff3864',
            'sidebar-section': '#b967ff',
            'muted': '#8a7ba8',
        },
    )


BUILTIN_THEMES: tuple[ThemeDefinition, ...] = (_sigil(), _dark(), _synthwave())
"""Themes that ship with Sigil and cannot be deleted from the picker."""

BUILTIN_NAMES = frozenset(theme.name for theme in BUILTIN_THEMES)


@dataclass
class ThemeRegistry:
    """Every theme available to the app: builtins first, then the installed ones.

    A user file with the same name as a builtin replaces it. That is the escape
    hatch for someone who wants the ``dark`` layout with their own colors, and it
    keeps the names the app defaults to always resolvable.
    """

    themes: dict[str, ThemeDefinition] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    """Files that could not be read, one message each. Shown, never fatal."""

    directory: Path | None = None
    """Where the installed themes were read from.

    It is remembered so a reload reads the same folder. Without it, `/theme` in a
    process whose themes live outside ``SIGIL_HOME`` would silently fall back to the
    default directory and the installed themes would vanish from the picker.
    """

    @classmethod
    def load(cls, directory: Path | None = None) -> ThemeRegistry:
        """Reads the builtins and whatever is installed in ``directory``."""
        folder = directory if directory is not None else themes_dir()
        registry = cls(themes={theme.name: theme for theme in BUILTIN_THEMES}, directory=folder)
        try:
            paths = sorted(folder.glob('*.json'))
        except OSError as exc:  # pragma: no cover - a directory that cannot be listed
            registry.errors.append(f'Cannot list {folder}: {exc}')
            return registry

        for path in paths:
            try:
                theme = load_file(path)
            except ThemeError as exc:
                registry.errors.append(str(exc))
                continue
            registry.themes[theme.name] = theme
        return registry

    def reload(self) -> ThemeRegistry:
        """Reads the same directory again, picking up files added while running."""
        return ThemeRegistry.load(self.directory)

    def names(self) -> list[str]:
        """Builtin names first, then the installed ones, alphabetically."""
        builtin = [theme.name for theme in BUILTIN_THEMES if theme.name in self.themes]
        installed = sorted(name for name in self.themes if name not in BUILTIN_NAMES)
        return builtin + installed

    def get(self, name: str) -> ThemeDefinition | None:
        return self.themes.get(name)

    def resolve(self, name: str) -> ThemeDefinition:
        """The named theme, or the default when it is not installed.

        A stored preference can name a theme the user has since removed; falling
        back is friendlier than refusing to start.
        """
        return self.themes.get(name) or self.themes[DEFAULT_THEME_NAME]


DEFAULT_THEME_NAME = BUILTIN_THEMES[0].name


def theme_variable_defaults() -> dict[str, str]:
    """Fallbacks for the variables ``theme.tcss`` uses.

    Textual fails to parse the stylesheet when a `$variable` exists neither in the
    active theme nor in the app's defaults, and the builtins are not the only
    themes that can be active. Declaring them here makes any theme file valid,
    even one that only sets ``primary``.
    """
    base = _dark()
    return {name: value for name, value in base.colors.items() if name not in ROLES}
