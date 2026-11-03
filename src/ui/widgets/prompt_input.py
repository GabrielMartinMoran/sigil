"""Input de la barra inferior con navegación del menú de comandos."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.widgets import Input


class PromptInput(Input):
    """Input siempre visible; ↑/↓/Enter/Esc operan el menú cuando está abierto."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('up', 'menu_up', show=False, priority=True),
        Binding('down', 'menu_down', show=False, priority=True),
        Binding('enter', 'menu_enter', show=False, priority=True),
        Binding('escape', 'menu_escape', show=False, priority=True),
    ]

    async def action_menu_up(self) -> None:
        self.app.menu_move(-1)

    async def action_menu_down(self) -> None:
        self.app.menu_move(1)

    async def action_menu_enter(self) -> None:
        if self.app.menu_visible:
            await self.app.menu_choose()
        else:
            await self.action_submit()

    async def action_menu_escape(self) -> None:
        self.app.menu_hide()
