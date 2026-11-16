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

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen

from src.core.theme_definition import ROLES, ThemeDefinition
from src.ui.widgets.theme_box import EMPTY, ThemeBox
from src.ui.widgets.theme_choice import ThemeChoice

"""Roles painted as swatches, in the order a reader scans them.

Six short blocks in six colors say more about a theme than a long bar in one: the
point is to see whether the *palette* is the one you want, not one hue of it.
"""

"""Left edge of the highlighted row. A bar, not an arrow: it marks a position."""

"""What the box says when the list is empty. Not an error: it cannot happen."""


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
