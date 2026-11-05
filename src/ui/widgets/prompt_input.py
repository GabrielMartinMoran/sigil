"""Bottom bar input with command menu navigation, the model shortcuts and abort.

Three things are handled here rather than as app-level bindings, and the reason
is the same for all of them: **the focused widget sees the key first**.

- The command menu (Up/Down/Tab/Enter/Esc) needs `priority`, because `Input`
  consumes those keys for editing and cursor movement.
- `ctrl+m` and `ctrl+t` are handled in `on_key`.
- `Esc` asks to stop the running turn, and any other key calls that request off.

A `priority` binding is checked by the app before the key reaches the widget, so
the actions below are where the menu keys land, not `on_key`. `on_key` sees
everything else: the printable keys, the editing keys, and the shortcuts.

Why `ctrl+m` needs care: in a legacy terminal, Ctrl+M and Enter are the same byte
(CR, `0x0D`), so a global `ctrl+m` binding silently swallows Enter — which is how a
message is sent. Textual enables the Kitty keyboard protocol with the
"disambiguate escape codes" flag, which makes a capable terminal send
`CSI 109;5u` for Ctrl+M (distinct from `\\r` for Enter), but terminals without it
still collapse the two. Handling the key here means:

- a capable terminal sends `ctrl+m` → the model picker opens;
- a legacy terminal sends `enter` → the message is sent, exactly as before.

Either way nothing is swallowed and Enter keeps working. `ctrl+t` has no such
ambiguity but is handled next to it so both live in one place.

Why `Esc` lives here and not in the app: the abort has to coexist with the `Esc`
that cancels a question panel, which is the panel's own binding. A global
`priority` binding at the app level would run before both of them and swallow
them.
"""

from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.binding import Binding
from textual.widgets import Input

CTRL_MODELS = 'ctrl+m'
"""Ctrl+M. Same byte as Enter on a legacy terminal; see the module docstring."""

CTRL_EFFORT = 'ctrl+t'
"""Ctrl+T. Free of ambiguity, but Ctrl+T is `transpose-chars` in readline."""


class PromptInput(Input):
    """Always visible input; Up/Down/Tab/Enter/Esc drive the menu when it is open."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('up', 'menu_up', show=False, priority=True),
        Binding('down', 'menu_down', show=False, priority=True),
        Binding('tab', 'menu_complete', show=False, priority=True),
        Binding('enter', 'menu_enter', show=False, priority=True),
        Binding('escape', 'menu_escape', show=False, priority=True),
    ]

    async def on_key(self, event: events.Key) -> None:
        """Ctrl+M / Ctrl+T open the pickers; anything else cancels a pending abort.

        They are read here, before the `Input` turns them into editing keys, and
        not bound globally: a global `ctrl+m` would claim Enter on any terminal
        that cannot tell them apart.

        The opener runs in a worker because the pickers use `push_screen_wait`,
        which requires one.
        """
        # Typing means "I am doing something else": a confirmation that survives
        # other keys would confirm itself by accident.
        self.app.cancel_abort()
        if event.key == CTRL_MODELS:
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.open_models())
            return
        if event.key == CTRL_EFFORT:
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.open_effort())

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

    async def action_menu_enter(self) -> None:
        self.app.cancel_abort()
        if self.app.menu_visible:
            await self.app.menu_choose()
        else:
            await self.action_submit()

    async def action_menu_escape(self) -> None:
        """Esc: closes the menu and asks to stop the turn.

        Both happen in one press. With no turn running the second part is a no-op,
        so `Esc` keeps behaving exactly as it did: it closes the menu.
        """
        self.app.menu_hide()
        if self.app.request_abort():
            self.app.abort_turn()
