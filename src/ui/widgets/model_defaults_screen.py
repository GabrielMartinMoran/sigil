"""The default model per agent, and the one a conversation starts on.

The list is a target per row — the default and every profile — and the file to write
is a decision of its own, taken with `Tab` and shown above the list. That split is
the point: the same entry can belong to the project or to the user, and which one is
right is not something a program can guess from the row the user is looking at.

The screen collects; it does not write. `Enter` returns the row and the destination,
and the command opens the model and effort pickers that `/model` already uses, so
there is one picker in the app and not two.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.agents.model_defaults import ConfigScope
from src.engine.agents.profile.models.agent_role import AgentRole
from src.ui.widgets.provider_modal import ProviderModal

SCOPE_TITLES: dict[ConfigScope, str] = {
    ConfigScope.PROJECT: 'project',
    ConfigScope.GLOBAL: 'global',
}

AGENT_SECTION = 'Agents'
SUBAGENT_SECTION = 'Subagents'
ORPHAN_SECTION = 'In the config, not installed'
"""The list's headings.

The two roles are kept apart because they behave differently: an agent is the one a
session runs as, a subagent only ever answers a summons, and a person choosing a
default for the second is choosing what its own runs use, not what to talk to.
"""


class DefaultsDestinations(Protocol):
    """Where a write can go, which is all the screen asks about a destination.

    The screen paints one line about the target file and refuses to clear an entry a
    file does not set, so it needs four facts: which files exist, where each one is,
    whether ``SIGIL_CONFIG`` replaced the pair, and what could not be parsed.

    It is a protocol and not `ModelDefaultsState` because the two modes answer it from
    different places. In process the state is read from the files; attached it is the
    service's projection, and the screen must not reach for the engine's reader to
    paint what the service already resolved. What is deliberately *not* here is
    ``effective``/``invalid``/``sources``: those are precedence, and each row arrives
    already carrying the answer the engine gave.
    """

    @property
    def destinations(self) -> tuple[ConfigScope, ...]:
        """The files a write may go to, in precedence order."""
        ...

    @property
    def fixed(self) -> Path | None:
        """The single explicit path, when ``SIGIL_CONFIG`` replaced the pair."""
        ...

    @property
    def errors(self) -> tuple[str, ...]:
        """What the files could not parse, in the order the screen lists it."""
        ...

    def path_for(self, scope: ConfigScope) -> Path:
        """The file ``scope`` names."""
        ...


@dataclass(frozen=True, slots=True)
class DefaultsRow:
    """One target: the default for new conversations, or an installed profile."""

    target: str | None
    label: str
    summary: str
    sources: frozenset[ConfigScope] = frozenset()
    invalid: bool = False
    active: bool = False
    installed: bool = True
    role: AgentRole = AgentRole.AGENT
    """Which kind of profile this is, for the section it is listed under.

    ``installed`` and the role answer different questions: a name in the config with
    no profile behind it has no role, and it is the one row worth clearing.
    """


@dataclass(frozen=True, slots=True)
class DefaultsChoice:
    """What the screen returns: which target, in which file, and what to do."""

    row: DefaultsRow
    scope: ConfigScope
    clear: bool = False


class ModelDefaultsScreen(ProviderModal):
    """Returns a `DefaultsChoice`, or ``None`` when the user closes it."""

    TITLE_TEXT = 'Default models'
    HINT_TEXT = '↑/↓ move · Tab destination · Enter set · d clear · Esc close'

    # Priority on the screen: the list has the focus, and `Tab` would move it
    # before the destination could change. Same reason the arrows are here.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'close', 'Close', show=False, priority=True),
        Binding('up', 'move(-1)', 'Up', show=False, priority=True),
        Binding('down', 'move(1)', 'Down', show=False, priority=True),
        Binding('pageup', 'move(-5)', 'Page up', show=False, priority=True),
        Binding('pagedown', 'move(5)', 'Page down', show=False, priority=True),
        Binding('tab', 'cycle_destination', 'Destination', show=False, priority=True),
        Binding('enter', 'edit', 'Set', show=False, priority=True),
        Binding('d', 'clear', 'Clear', show=False, priority=True),
        Binding('delete', 'clear', 'Clear', show=False, priority=True),
    ]

    def __init__(self, rows: Sequence[DefaultsRow], state: DefaultsDestinations) -> None:
        super().__init__()
        self.rows = list(rows)
        self._state = state
        self._destination = state.destinations[0]
        self._notice = ''

    def compose_body(self) -> ComposeResult:
        yield Static('', id='defaults-status')
        yield OptionList(*(Option('', id=str(index)) for index in range(len(self.rows))), id='defaults-list')
        yield Static('', id='defaults-error')

    def on_mount(self) -> None:
        if self.rows:
            options = self.query_one(OptionList)
            options.highlighted = 0
            self._follow(self.rows[0])
            options.focus()
        self._paint()

    # --- painting ----------------------------------------------------------

    def _paint(self) -> None:
        """Redraws the rows and the destination line from the current state."""
        options = self.query_one('#defaults-list', OptionList)
        previous = ''
        for index, row in enumerate(self.rows):
            section = _section_title(row)
            options.replace_option_prompt(str(index), _row(row, section if section != previous else None))
            previous = section
        self.query_one('#defaults-status', Static).update(self._status())
        errors = self._state.errors
        self.query_one('#defaults-error', Static).display = bool(errors)
        if errors:
            self.query_one('#defaults-error', Static).update('\n'.join(f'✖ {error}' for error in errors))

    def _status(self) -> str:
        scope = self._destination
        path = self._state.path_for(scope)
        head = f'[bold]Destination:[/bold] {_destination_title(self._state, scope)}  [dim]{path}[/dim]'
        if self._notice:
            return f'{head}\n[yellow]{self._notice}[/yellow]'
        if self._state.fixed is not None:
            return f'{head}\n[dim]SIGIL_CONFIG replaces both files, so there is nowhere else to write.[/dim]'
        return f'{head}\n[dim]Enter sets the entry there · d clears it there · Tab switches file[/dim]'

    # --- moving ------------------------------------------------------------

    def _selected(self) -> DefaultsRow | None:
        index = self.query_one(OptionList).highlighted
        return self.rows[index] if index is not None and index < len(self.rows) else None

    def _follow(self, row: DefaultsRow) -> None:
        """Starts the destination where the entry lives, as the user asked for.

        Moving to a row that the project sets offers to edit the project file; moving
        to one that only the user's file sets offers that one. The destination follows
        the row because that is the least surprising default — an entry is where it is.
        """
        for scope in self._state.destinations:
            if scope in row.sources:
                self._destination = scope
                return
        self._destination = self._state.destinations[-1]

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        if not options.option_count:
            return
        options.highlighted = min(max((options.highlighted or 0) + delta, 0), options.option_count - 1)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id is None:
            return
        index = int(str(event.option.id))
        if index < len(self.rows):
            self._notice = ''
            self._follow(self.rows[index])
            self._paint()

    def action_cycle_destination(self) -> None:
        destinations = self._state.destinations
        if len(destinations) < 2:
            return
        current = destinations.index(self._destination)
        self._destination = destinations[(current + 1) % len(destinations)]
        self._notice = ''
        self._paint()

    # --- choosing ----------------------------------------------------------

    def action_edit(self) -> None:
        row = self._selected()
        if row is not None:
            self.dismiss(DefaultsChoice(row, self._destination))

    def action_clear(self) -> None:
        row = self._selected()
        if row is None:
            return
        if self._destination not in row.sources:
            self._notice = f'Nothing to clear: {_destination_title(self._state, self._destination)} does not set it.'
            self._paint()
            return
        self.dismiss(DefaultsChoice(row, self._destination, clear=True))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # A click lands here and not on the `enter` binding; both doors end in the
        # same place. A key press never arrives twice: the priority binding is what
        # consumes `enter`, so the list never selects on its own.
        if event.option.id is not None:
            self.action_edit()

    def action_close(self) -> None:
        self.dismiss(None)


def _destination_title(state: DefaultsDestinations, scope: ConfigScope) -> str:
    return 'SIGIL_CONFIG' if state.fixed is not None else SCOPE_TITLES[scope]


def _section_title(row: DefaultsRow) -> str:
    """The heading a row belongs under; the default heads itself."""
    if row.target is None:
        return ''
    if not row.installed:
        return ORPHAN_SECTION
    return AGENT_SECTION if row.role is AgentRole.AGENT else SUBAGENT_SECTION


def _row(row: DefaultsRow, section: str | None = None) -> Text:
    """One row: the heading it opens, the target, and which file says so."""
    text = Text()
    if section:
        text.append(f'{section}\n', style='bold cyan')
    text.append('● ' if row.active else '  ', style='green' if row.active else 'dim')
    text.append(row.label, style='bold')
    if not row.installed:
        text.append('  (not installed)', style='dim')
    badge = _badge(row)
    if badge:
        text.append(f'  {badge}', style='yellow' if row.invalid else 'cyan')
    text.append(f'\n    {row.summary}', style='red' if row.invalid else 'dim')
    return text


def _badge(row: DefaultsRow) -> str:
    """Which file sets the row, and whether it is a value the files could not read.

    Both, when both apply: an invalid entry still belongs to a file, and the file is
    what the user has to clear to get rid of it.
    """
    parts: list[str] = []
    if row.sources == {ConfigScope.PROJECT, ConfigScope.GLOBAL}:
        parts.append('project, overrides global')
    else:
        parts.extend(SCOPE_TITLES[scope] for scope in (ConfigScope.PROJECT, ConfigScope.GLOBAL) if scope in row.sources)
    if row.invalid:
        parts.append('invalid')
    return ', '.join(parts)


__all__ = ['DefaultsChoice', 'DefaultsRow', 'ModelDefaultsScreen']
