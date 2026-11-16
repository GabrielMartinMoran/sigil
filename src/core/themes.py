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

from src.core.theme_definition import ROLES
from src.core.theme_registry import _dark

"""Color keys that become the theme palette instead of CSS variables."""


"""Themes that ship with Sigil and cannot be deleted from the picker."""


def theme_variable_defaults() -> dict[str, str]:
    """Fallbacks for the variables ``theme.tcss`` uses.

    Textual fails to parse the stylesheet when a `$variable` exists neither in the
    active theme nor in the app's defaults, and the builtins are not the only
    themes that can be active. Declaring them here makes any theme file valid,
    even one that only sets ``primary``.
    """
    base = _dark()
    return {name: value for name, value in base.colors.items() if name not in ROLES}
