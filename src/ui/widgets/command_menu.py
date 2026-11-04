"""Dropdown command menu with filtering and internal scroll."""

from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from src.engine.commands import Command


class CommandMenu(OptionList):
    """List of commands matching what was typed; it shows above the input."""

    def update_matches(self, commands: list[Command]) -> None:
        self.clear_options()
        for command in commands:
            self.add_option(Option(f'[bold]{command.name}[/bold]   [dim]{command.description}[/dim]', id=command.name))
        self.highlighted = 0 if commands else None
