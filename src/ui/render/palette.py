"""Named colors of the active theme, for widgets that paint with Rich.

The stylesheet handles everything that is a box. What is left is text assembled in
Python — the tool call gutter, the reasoning marker, the diff counters — and Rich
does not understand Textual's `$variables`. These helpers read the resolved theme
so a custom theme file reaches that text too, instead of leaving it on hardcoded
colors that only look right on the theme they were written for.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

UNKNOWN = ''

_FALLBACKS: dict[str, str] = {
    'primary': 'cyan',
    'secondary': 'blue',
    'accent': 'cyan',
    'foreground': 'white',
    'panel': 'black',
    'success': 'green',
    'warning': 'yellow',
    'error': 'red',
    'tool-border': 'bright_black',
    'tool-title': 'cyan',
    'tool-output': 'bright_black',
    'reasoning': 'bright_black',
    'diff-added': 'green',
    'diff-removed': 'red',
    'sidebar-section': 'bright_black',
    'muted': 'bright_black',
}
"""What a color name looks like when the theme does not define it.

They are ANSI names rather than hex so the fallback stays legible on a terminal
whose palette the theme author never saw.
"""


def color(widget: Any, name: str) -> str:
    """Resolved color ``name`` of the theme active in ``widget``'s app.

    It is deliberately forgiving: the renderers run from unit tests, from a
    pre-mount callback and from a widget whose app is shutting down, and none of
    those is a reason for a panel to fail to paint. Without a theme it falls back
    to the ANSI name, which is what the widget looked like before themes existed.
    """
    try:
        variables = getattr(widget.app, 'theme_variables', None) or {}
    except Exception:  # noqa: BLE001 - no active app, or one being torn down
        return _FALLBACKS.get(name, UNKNOWN)
    value = variables.get(name)
    if isinstance(value, str) and value:
        return value
    return _FALLBACKS.get(name, UNKNOWN)


def themed(widget: Any, name: str) -> str:
    """Alias kept short for the renderers, which call it on every line."""
    return color(widget, name)


def join(parts: list[tuple[str, str]]) -> Text:
    """Builds a `Text` from ``(content, style)`` pairs, skipping empty content.

    It exists so the renderers read as a description of the line instead of a
    sequence of `append` calls with a mutable cursor.
    """
    text = Text()
    for content, style in parts:
        if content:
            text.append(content, style=style)
    return text
