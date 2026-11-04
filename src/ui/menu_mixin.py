"""Menú de comandos que aparece al escribir `/` en el input."""

from __future__ import annotations

from textual.widgets import Input

from src.engine.commands import match_commands
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput


class MenuMixin:
    """Muestra y navega el menú de comandos.

    Espera que la app defina ``menu_visible`` y ``handle_command``.
    """

    menu_visible: bool

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is not self.query_one(PromptInput):
            return

        matches = match_commands(event.value)
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
        self.query_one(CommandMenu).display = False
        self.menu_visible = False

    async def menu_choose(self) -> None:
        menu = self.query_one(CommandMenu)
        option = menu.highlighted_option
        if option is None or option.id is None:
            return
        self.menu_hide()
        self.query_one(PromptInput).value = ''
        # En un worker para poder usar `push_screen_wait` (p. ej. /load).
        self.run_worker(self.handle_command(option.id))
