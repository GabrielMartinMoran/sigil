"""Command menu that appears when typing `/` in the input."""

from __future__ import annotations

import shlex

from textual.css.query import NoMatches
from textual.widgets import TextArea

from src.engine.commands import match_commands
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput

ARITY_ONE = frozenset({'/model', '/effort', '/theme', '/reasoning-visibility'})
"""Commands whose first argument is a single word worth completing verbatim."""


class MenuMixin:
    """Shows and navigates the command menu.

    It expects the app to define ``menu_visible`` and ``handle_command``.
    """

    menu_visible: bool

    async def open_command_palette(self) -> None:
        from src.ui.widgets.command_palette_screen import CommandPaletteScreen
        from src.ui.widgets.text_prompt_screen import TextPromptScreen

        choice = await self.push_screen_wait(CommandPaletteScreen())
        if choice is None:
            return
        args = ''
        if choice.worktree:
            name = await self.push_screen_wait(TextPromptScreen(title='New worktree', placeholder='Worktree name…'))
            if name is None:
                return
            args = f'--worktree {shlex.quote(name)}'
        await self.handle_command(choice.name, args)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """The composer changed: refilter the menu.

        `TextArea` posts `Changed` for every edit, including the one `clear()`
        makes when a message is sent, so the menu closes on send without the
        submit path having to say so.

        The menu belongs to the **name**, not to the arguments: it closes as soon
        as the text holds anything past the first word. Otherwise a command typed
        with an argument keeps one row highlighted, `Enter` picks that row, and the
        argument is dropped — `/agent reviewer` ran `/agent`, `/sidebar off` toggled
        instead of setting. The names are complete by then; there is nothing left
        to choose between.
        """
        if not isinstance(event.text_area, PromptInput):
            return

        text = event.text_area.text
        name = text.split(maxsplit=1)[0] if text.strip() else ''
        if text.strip() and text.strip() != name:
            # An argument was typed (or a trailing space was completed in): the
            # command is decided, and what comes next is not the menu's business.
            self.menu_hide()
            return

        matches = match_commands(text)
        menu = self.query_one(CommandMenu)
        if matches:
            menu.update_matches(matches)
            menu.display = True
            self.menu_visible = True
        else:
            menu.display = False
            self.menu_visible = False

    def menu_move(self, direction: int) -> None:
        if not self.menu_visible:
            return
        menu = self.query_one(CommandMenu)
        if direction < 0:
            menu.action_cursor_up()
        else:
            menu.action_cursor_down()

    def menu_hide(self) -> None:
        """Hides the menu, whether or not it is mounted.

        The composer writes its value from several paths — restoring a message that
        missed its seam, a command handed back, a session switch — and some of them
        run before the widget tree is composed. A menu that is not there is already
        hidden.
        """
        self.menu_visible = False
        try:
            menu = self.query_one(CommandMenu)
        except NoMatches:
            return
        menu.display = False

    def menu_complete(self) -> None:
        """Tab in the menu: writes the highlighted command into the input.

        It does not close the menu. After completing `/new` there is nothing left to
        pick, so the menu filters itself down to that one entry; after completing
        `/model` the menu is gone (the typed text no longer matches a command name),
        which is exactly what should happen before typing the argument.
        """
        menu = self.query_one(CommandMenu)
        option = menu.highlighted_option
        if option is None or option.id is None:
            return

        name = str(option.id)
        field = self.query_one(PromptInput)
        field.value = f'{name} ' if name in ARITY_ONE else name
        field.move_cursor(field.document.end)

    async def menu_choose(self) -> None:
        menu = self.query_one(CommandMenu)
        option = menu.highlighted_option
        if option is None or option.id is None:
            return
        self.menu_hide()
        field = self.query_one(PromptInput)
        field.remember_submission(str(option.id))
        field.clear()
        # In a worker to be able to use `push_screen_wait` (e.g. /load).
        self.run_worker(self.handle_command(option.id))
