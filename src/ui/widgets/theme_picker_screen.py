"""Theme picker: move through the list and see the app change underneath.

The point of a theme is how the whole interface looks, not what the swatches say, so
the screen does not try to preview it in a box. It floats over the real app and
applies each theme as the highlight moves; cancelling puts the original back. What
you see while moving is what you get on Enter.

The whole box is one `Static` that paints its own rows, and the screen keeps the
highlight. That is deliberate. The obvious build — an `OptionList` inside a
`Vertical` inside a modal — hangs Textual's message loop when the screen is popped:
the pop waits for a layout, the theme change waits for the pop, and the loop spins
on both. One widget with no focusable child has no such cycle, and it also allows the
two-line rows a theme needs (a name and its palette) that an `OptionList` cannot
draw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from src.engine.themes import ROLES, ThemeDefinition

SWATCHES = ('primary', 'accent', 'secondary', 'success', 'warning', 'error')
"""Roles painted as swatches, in the order a reader scans them.

Six short blocks in six colors say more about a theme than a long bar in one: the
point is to see whether the *palette* is the one you want, not one hue of it.
"""

MARKER = '▌'
"""Left edge of the highlighted row. A bar, not an arrow: it marks a position."""

EMPTY = 'No themes installed.'
"""What the box says when the list is empty. Not an error: it cannot happen."""


@dataclass(frozen=True, slots=True)
class ThemeChoice:
    """The theme the user settled on."""

    name: str


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


class ThemePickerScreen(ModalScreen[ThemeChoice | None]):
    """Returns the chosen theme, or ``None`` when cancelled.

    It previews but does not decide: on highlight it applies the theme so the whole
    app repaints underneath, and on cancel it returns ``None``. Putting the original
    theme back is the caller's job, because it has to happen *after* the screen is
    gone — setting a theme schedules a CSS refresh, and doing that while the screen
    is still coming down is the other half of the cycle described above.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False, priority=True),
        Binding('up', 'move(-1)', 'Up', show=False, priority=True),
        Binding('down', 'move(1)', 'Down', show=False, priority=True),
        Binding('enter', 'choose', 'Apply', show=False, priority=True),
    ]

    def __init__(self, themes: list[ThemeDefinition], current: str) -> None:
        super().__init__()
        self._themes = themes
        self._current = current
        self._original = current

    @property
    def original(self) -> str:
        """Theme that was active when the screen opened, for the caller to restore."""
        return self._original

    def compose(self) -> ComposeResult:
        yield Vertical(ThemeBox(self._themes, id='theme-box'), id='theme-wrap')

    def on_mount(self) -> None:
        box = self.query_one(ThemeBox)
        start = next((i for i, theme in enumerate(self._themes) if theme.name == self._current), 0)
        box.select(start)
        self._preview(box.highlighted_theme())

    # --- preview ------------------------------------------------------------

    def _preview(self, theme: ThemeDefinition | None) -> None:
        """Applies ``theme`` and reports it. The only place the app theme moves."""
        box = self.query_one(ThemeBox)
        if theme is None:
            box.set_status(Text(EMPTY, style='dim'))
            return
        self._current = theme.name
        self.app.theme = theme.name
        box.set_in_use(theme.name)
        box.set_status(self._status_for(theme))

    def _status_for(self, theme: ThemeDefinition) -> Text:
        missing = [role for role in ROLES if role not in theme.colors]
        line = Text()
        line.append(theme.name, style='bold')
        if theme.name == self._original:
            line.append('  was in use', style='dim')
        line.append(f'  {len(theme.colors)} colors', style='dim')
        if missing:
            # Worth saying: an incomplete theme still works, but it inherits the
            # rest from Textual's defaults and may look unlike what was intended.
            line.append(f'  · {len(missing)} roles inherited', style='dim')
        return line

    # --- actions ------------------------------------------------------------

    def action_move(self, delta: int) -> None:
        theme = self.query_one(ThemeBox).move(delta)
        if theme is not None:
            self._preview(theme)

    def action_choose(self) -> None:
        theme = self.query_one(ThemeBox).highlighted_theme()
        if theme is None:
            self.dismiss(None)
            return
        self.dismiss(ThemeChoice(name=theme.name))

    def action_cancel(self) -> None:
        """Escape closes without deciding. The caller restores the theme."""
        self.dismiss(None)
