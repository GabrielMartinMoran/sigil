"""The preview box a theme row paints."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from src.core.theming.theme_definition import ThemeDefinition

SWATCHES = ('primary', 'accent', 'secondary', 'success', 'warning', 'error')

MARKER = '▌'

EMPTY = 'No themes installed.'


class ThemeBox(Static):
    """The whole picker: title, status, rows and hint, painted as one renderable.

    Keeping it in one widget is what avoids the pop/layout cycle described in the
    module docstring. The screen owns the highlight; this widget only draws it.
    """

    def __init__(self, themes: list[ThemeDefinition], **kwargs: object) -> None:
        super().__init__(Text(''), **kwargs)
        self._themes = themes
        self._highlighted = 0
        self._in_use = ''
        self._status = Text('')

    @property
    def count(self) -> int:
        return len(self._themes)

    @property
    def highlighted(self) -> int:
        return self._highlighted

    def highlighted_theme(self) -> ThemeDefinition | None:
        if not self._themes:
            return None
        return self._themes[self._highlighted]

    def select(self, index: int) -> ThemeDefinition | None:
        """Moves the highlight, clamped to the list, and repaints."""
        if not self._themes:
            return None
        self._highlighted = min(max(index, 0), len(self._themes) - 1)
        self.refresh_content()
        return self.highlighted_theme()

    def move(self, delta: int) -> ThemeDefinition | None:
        """Moves by ``delta``. It does not wrap: a list of five is not a carousel."""
        return self.select(self._highlighted + delta)

    def set_in_use(self, name: str) -> None:
        self._in_use = name
        self.refresh_content()

    def set_status(self, text: Text) -> None:
        self._status = text
        self.refresh_content()

    def refresh_content(self) -> None:
        self.update(self._build())

    def on_mount(self) -> None:
        self.refresh_content()

    def _build(self) -> Text:
        text = Text()
        text.append('Theme\n\n', style='bold')
        if self._status.plain:
            text.append_text(self._status)
            text.append('\n\n')
        if not self._themes:
            text.append(f'{EMPTY}\n\n', style='dim')
        for index, theme in enumerate(self._themes):
            self._append_row(text, theme, selected=index == self._highlighted)
        text.append('\n')
        text.append('↑/↓ preview · Enter apply · Esc cancel', style='dim')
        return text

    def _append_row(self, text: Text, theme: ThemeDefinition, *, selected: bool) -> None:
        text.append(f'{MARKER} ' if selected else '  ', style='bold')
        text.append(theme.name, style='bold reverse' if selected else 'bold')
        if theme.name == self._in_use:
            text.append('  in use', style='green')
        text.append('\n')

        text.append(f'{MARKER} ' if selected else '  ')
        for role in SWATCHES:
            value = theme.colors.get(role)
            if value:
                text.append('██', style=value)
                text.append(' ')
        text.append(' ')
        text.append('builtin' if theme.builtin else 'installed', style='dim')
        text.append('\n')
