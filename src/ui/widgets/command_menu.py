"""Dropdown command menu with filtering and internal scroll."""

from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from src.engine.commands import Command
from src.ui.command_transport import listing_mark


class CommandMenu(OptionList):
    """List of commands matching what was typed; it shows above the input."""

    def update_matches(self, commands: list[Command], *, attached: bool = False) -> None:
        """Paints the matching commands, marking the ones this terminal cannot act on.

        ``attached`` is the caller's to know, not the menu's: the mixin that owns the
        menu holds the client, and a widget guessing at the wiring from the app it is
        mounted in would be a second place to keep in step with the transport table.

        The mark is `listing_mark`'s and the sentence is the catalogue's, so the menu
        promises exactly what the dispatcher will honour — attached, the one command
        whose effect no route carries says so before it is picked.
        """
        self.clear_options()
        for command in commands:
            self.add_option(
                Option(
                    f'[bold]{command.name}[/bold]   [dim]{command.description}[/dim]'
                    f'{listing_mark(command.name, attached=attached)}',
                    id=command.name,
                )
            )
        self.highlighted = 0 if commands else None
