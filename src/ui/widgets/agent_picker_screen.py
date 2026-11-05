"""Agent picker: choose which profile sets Sigil's behaviour.

It is the same room as the model and the effort pickers — `/agent` and `ctrl+a`
open it — because choosing an agent is the same kind of decision: one list, one
Enter, and the chrome tells you what is in effect afterwards.

Only installed profiles are listed. There is no free-text entry: a model id can
be typed ahead of a catalogue, but an agent is a file with a prompt in it, and
guessing a name that does not exist would leave the session with no behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from src.engine.agents.profile.models.agent_profile import AgentProfile
from src.ui.widgets.provider_modal import ProviderModal


@dataclass(frozen=True, slots=True)
class AgentChoice:
    """The profile the user picked."""

    name: str


class AgentPickerScreen(ProviderModal):
    """Returns the chosen agent name, or ``None``."""

    TITLE_TEXT = 'Agent'
    HINT_TEXT = 'Type to filter · ↑/↓ move · Enter select · Esc cancel'

    # Priority on the screen: the focus is the filter, so without it the arrows
    # never reach the list and it looks frozen.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False, priority=True),
        Binding('up', 'move(-1)', 'Up', show=False, priority=True),
        Binding('down', 'move(1)', 'Down', show=False, priority=True),
        Binding('pageup', 'move(-5)', 'Page up', show=False, priority=True),
        Binding('pagedown', 'move(5)', 'Page down', show=False, priority=True),
        Binding('enter', 'choose', 'Choose', show=False, priority=True),
    ]

    def __init__(self, profiles: Sequence[AgentProfile], *, current: str = '') -> None:
        super().__init__()
        self._profiles = list(profiles)
        self._current = current
        self._visible: list[AgentProfile] = []

    def compose_body(self) -> ComposeResult:
        yield Static('', id='agent-status')
        yield Input(placeholder='Filter by name…', id='agent-filter')
        yield OptionList(id='agent-list')

    def on_mount(self) -> None:
        self._refresh_list('')
        self.query_one('#agent-filter', Input).focus()

    # --- filtering ---------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != 'agent-filter':
            return
        self._refresh_list(event.value)

    def _refresh_list(self, query: str) -> None:
        needle = query.strip().lower()
        self._visible = [
            profile
            for profile in self._profiles
            if not needle or needle in profile.name.lower() or needle in profile.description.lower()
        ]
        options = self.query_one(OptionList)
        options.clear_options()
        for profile in self._visible:
            options.add_option(Option(_row(profile, current=self._current), id=profile.name))
        self._update_status(needle)
        if self._visible:
            options.highlighted = self._current_index()

    def _current_index(self) -> int:
        for index, profile in enumerate(self._visible):
            if profile.name == self._current:
                return index
        return 0

    def _update_status(self, needle: str) -> None:
        status = self.query_one('#agent-status', Static)
        if not self._profiles:
            status.update(
                '[yellow]No agent profiles found. Add one at .sigil/agents/<name>.md (project) '
                'or ~/.sigil/agents/<name>.md (global).[/yellow]'
            )
            return
        if not self._visible:
            status.update(f'[yellow]No agent matches “{needle}”. Esc cancels.[/yellow]')
            return
        status.update(f'[dim]{len(self._visible)} of {len(self._profiles)} agents[/dim]')

    # --- actions -----------------------------------------------------------

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        count = options.option_count
        if not count:
            return
        current = options.highlighted or 0
        options.highlighted = min(max(current + delta, 0), count - 1)

    def action_choose(self) -> None:
        option = self.query_one(OptionList).highlighted_option
        if option is None or option.id is None:
            return
        self.dismiss(AgentChoice(name=str(option.id)))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # The screen-level `enter` binding already handles this; swallowing the
        # submit here keeps the Input from doing its own.
        if event.input.id == 'agent-filter':
            self.action_choose()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(AgentChoice(name=str(event.option.id)))

    def action_cancel(self) -> None:
        self.dismiss(None)


def _row(profile: AgentProfile, *, current: str) -> str:
    marker = '[green]●[/green] ' if profile.name == current else '  '
    origin = {'builtin': 'built-in', 'project': 'project', 'global': 'global'}.get(profile.source, profile.source)
    description = ' '.join(profile.description.split())
    return (
        f'{marker}[bold]{profile.label}[/bold] [dim]{profile.name} · {origin}[/dim]\n'
        f'    [dim]{description}[/dim]\n'
        f'    [dim]{profile.permissions.describe()}[/dim]'
    )
