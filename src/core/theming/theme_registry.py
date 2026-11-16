"""The ThemeRegistry value of the core layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.core.config.paths import themes_dir
from src.core.theming.load_file import load_file
from src.core.theming.theme_definition import ThemeDefinition
from src.core.theming.theme_error import ThemeError


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
