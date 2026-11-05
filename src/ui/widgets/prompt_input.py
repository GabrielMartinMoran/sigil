"""Bottom bar input: a multi-line composer with the command menu and shortcuts.

Four things are handled here rather than as app-level bindings, and the reason is
the same for all of them: **the focused widget sees the key first**.

- The command menu (Up/Down/Tab/Esc) needs `priority`, because the text area
  consumes those keys for editing and cursor movement.
- `ctrl+m`, `ctrl+t` and `ctrl+a` are handled in `_on_key`.
- `Enter` sends and `Shift+Enter` inserts a newline. The text area already treats
  Enter as a newline, so the split is decided in `_on_key`, before the area turns
  the key into an edit.
- `Esc` asks to stop the running turn, and any other key calls that request off.

A `priority` binding is checked by the app before the key reaches the widget, so
the actions below are where the menu keys land, not `_on_key`. `_on_key` sees
everything else: the printable keys, the editing keys, and the shortcuts.

Why `ctrl+m` needs care: in a legacy terminal, Ctrl+M and Enter are the same byte
(CR, `0x0D`), so a global `ctrl+m` binding silently swallows Enter — which is how a
message is sent. Textual enables the Kitty keyboard protocol with the
"disambiguate escape codes" flag, which makes a capable terminal send
`CSI 109;5u` for Ctrl+M (distinct from `\\r` for Enter), but terminals without it
still collapse the two. Handling the key here means:

- a capable terminal sends `ctrl+m` → the model picker opens;
- a legacy terminal sends `enter` → the message is sent, exactly as before.

`Shift+Enter` has a related, milder constraint: it only exists as a distinct key on
a terminal that reports modifiers. Without them the terminal sends a bare CR and
the key reads as `enter`, which sends the message. That degrades into the familiar
behaviour rather than into a lost keystroke, so it needs no fallback.

Why `Esc` lives here and not in the app: the abort has to coexist with the `Esc`
that cancels a question panel, which is the panel's own binding. A global
`priority` binding at the app level would run before both of them and swallow
them.
"""

from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

CTRL_MODELS = 'ctrl+m'
"""Ctrl+M. Same byte as Enter on a legacy terminal; see the module docstring."""

CTRL_EFFORT = 'ctrl+t'
"""Ctrl+T. Free of ambiguity, but Ctrl+T is `transpose-chars` in readline."""

CTRL_AGENT = 'ctrl+a'
"""Ctrl+A. `home` in the `TextArea` (and in readline), which is why it is read
here: the prompt claims it before the widget turns it into a cursor move."""

NEWLINE_KEYS = frozenset({'shift+enter'})
"""Keys that insert a line break instead of sending. See the module docstring."""


class PromptInput(TextArea):
    """The message composer: Enter sends, Shift+Enter opens a new line.

    It is a `TextArea` and not an `Input` because a message with line breaks is a
    document, not a field: a single-line field cannot show the second line, and
    the alternative — a hidden buffer with a fake display — would have to
    reimplement wrapping, selection and cursor movement.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('up', 'menu_up', show=False, priority=True),
        Binding('down', 'menu_down', show=False, priority=True),
        Binding('tab', 'menu_complete', show=False, priority=True),
        Binding('escape', 'menu_escape', show=False, priority=True),
    ]

    class Submitted(Message):
        """The user pressed Enter with a message worth sending.

        It carries the text and not the widget: by the time the app handles it the
        composer is already empty, and the turn needs what was in it.
        """

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(
        self,
        *,
        placeholder: str = '',
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            id=id,
            classes=classes,
            disabled=disabled,
            soft_wrap=True,
            show_line_numbers=False,
            # Tab belongs to the command menu, never to the document.
            tab_behavior='focus',
            highlight_cursor_line=False,
        )

    # --- keys ---------------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        """Enter sends, Shift+Enter breaks the line, and the shortcuts open screens.

        It runs before `TextArea`'s own handler, which would insert `\\n` for Enter
        and consume the key. The picker openers run in a worker because they use
        `push_screen_wait`, which requires one.
        """
        # Typing means "I am doing something else": a confirmation that survives
        # other keys would confirm itself by accident.
        self.app.cancel_abort()
        if event.key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.insert('\n')
            return

        if event.key == 'enter':
            event.stop()
            event.prevent_default()
            if self.app.menu_visible:
                # Enter on an open menu picks the highlighted row; that path goes
                # through `menu_choose`, which is guarded like the typed one.
                await self.app.menu_choose()
            else:
                await self.action_submit()
            return

        if event.key == CTRL_MODELS:
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.open_models())
            return

        if event.key == CTRL_EFFORT:
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.open_effort())
            return
        if event.key == CTRL_AGENT:
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.open_agent())
            return

        await super()._on_key(event)

    # --- value --------------------------------------------------------------

    @property
    def value(self) -> str:
        """The text in the composer.

        `TextArea` calls its content `text`; the app and the tests speak of the
        value of a field, and keeping that name means the composer can replace an
        `Input` without the callers noticing.
        """
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.load_text(text)
        self.move_cursor(self.document.end)

    def clear(self) -> None:
        """Empties the composer and puts the cursor back at the start."""
        self.load_text('')
        self.move_cursor((0, 0))

    # --- actions ------------------------------------------------------------

    async def action_submit(self) -> None:
        """Sends what is in the composer, unless it is only whitespace."""
        text = self.text.strip()
        if not text:
            return
        self.clear()
        self.post_message(self.Submitted(text))

    async def action_menu_up(self) -> None:
        self.app.cancel_abort()
        self.app.menu_move(-1)

    async def action_menu_down(self) -> None:
        self.app.cancel_abort()
        self.app.menu_move(1)

    async def action_menu_complete(self) -> None:
        """Tab completes the highlighted command instead of running it.

        `Enter` is the one that executes; Tab only fills the word in, and appends a
        trailing space when the command takes an argument, so the next keystroke is
        the argument rather than the rest of the name.
        """
        self.app.cancel_abort()
        if self.app.menu_visible:
            self.app.menu_complete()
            return
        # No menu: nothing to complete, and the focus must stay in the prompt.
        self.app.action_tab_noop()

    async def action_menu_escape(self) -> None:
        """Esc: closes the menu and asks to stop the turn.

        Both happen in one press. With no turn running the second part is a no-op,
        so `Esc` keeps behaving exactly as it did: it closes the menu.
        """
        self.app.menu_hide()
        if self.app.request_abort():
            self.app.abort_turn()
