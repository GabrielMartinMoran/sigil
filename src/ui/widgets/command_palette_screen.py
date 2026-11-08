"""A searchable view over the same commands accepted by the composer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from src.engine.commands import COMMANDS
from src.ui.widgets.provider_modal import ProviderModal

COMMAND_LABELS = {
    '/new': 'New session',
    '/load': 'Load session',
    '/agent': 'Choose agent',
    '/provider': 'Manage providers',
    '/model': 'Choose model',
    '/effort': 'Choose reasoning effort',
    '/mcp': 'Show MCP servers',
    '/context': 'Show context usage',
    '/rollback': 'Roll back or fork',
    '/reasoning-visibility': 'Toggle reasoning',
    '/tools': 'Toggle tool output',
    '/theme': 'Choose theme',
    '/sidebar': 'Toggle sidebar',
    '/alerts': 'Manage alerts',
    '/debug': 'Toggle debug view',
    '/help': 'Show help',
    '/quit': 'Quit Sigil',
}


@dataclass(frozen=True, slots=True)
class PaletteChoice:
    name: str
    worktree: bool = False


class CommandPaletteScreen(ProviderModal):
    TITLE_TEXT = 'Commands'
    HINT_TEXT = 'Type to filter · ↑/↓ move · Enter select · Esc cancel'
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', show=False, priority=True),
        Binding('up', 'move(-1)', show=False, priority=True),
        Binding('down', 'move(1)', show=False, priority=True),
        Binding('pageup', 'move(-5)', show=False, priority=True),
        Binding('pagedown', 'move(5)', show=False, priority=True),
        Binding('enter', 'choose', show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._items = [
            (
                PaletteChoice(command.name),
                COMMAND_LABELS.get(command.name, command.name.lstrip('/').replace('-', ' ').capitalize()),
                command.name,
                command.description,
            )
            for command in COMMANDS
        ]
        self._items.insert(
            1,
            (
                PaletteChoice('/new', worktree=True),
                'New session in a worktree',
                '/new --worktree',
                'Start a session in a new worktree',
            ),
        )
        self._visible: list[tuple[PaletteChoice, str, str, str]] = []

    def compose_body(self) -> ComposeResult:
        yield Input(placeholder='Find a command…', id='palette-filter')
        yield Static('', id='palette-status')
        yield OptionList(id='palette-commands')

    def on_mount(self) -> None:
        self._refresh('')
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def _refresh(self, query: str) -> None:
        needle = query.casefold().strip()
        self._visible = [item for item in self._items if needle in f'{item[1]} {item[2]} {item[3]}'.casefold()]
        if needle:
            self._visible.sort(key=lambda item: needle not in f'{item[1]} {item[2]}'.casefold())
        options = self.query_one(OptionList)
        options.clear_options()
        for index, (_, label, command, description) in enumerate(self._visible):
            row = Text(label, style='bold')
            row.append(f' ({command})', style='dim')
            row.append(f'\n  {description}', style='dim')
            options.add_option(Option(row, id=str(index)))
        options.highlighted = 0 if self._visible else None
        self.query_one('#palette-status', Static).update(f'{len(self._visible)} commands')

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        if options.option_count:
            options.highlighted = min(max((options.highlighted or 0) + delta, 0), options.option_count - 1)

    def action_choose(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is not None and index < len(self._visible):
            self.dismiss(self._visible[index][0])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        del event
        self.action_choose()

    def action_cancel(self) -> None:
        self.dismiss(None)
