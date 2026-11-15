"""Going back to a message: pick a point, then say what to do with it.

Two things can happen at the same instant, and the screen makes the difference
visible instead of hiding it behind one word:

- **Roll back** destroys the turns after it and puts the workspace back. That is the
  destructive one, and it says how many files it would touch before doing it.
- **Fork** opens a new session with the same prefix and leaves the original whole. It
  does not touch the workspace, so it needs no warning.

The file count comes from the same diff the restore would apply, computed on demand:
a rollback that says "3 files would change" is a confirmation with substance, and one
that says "no files" is telling you the truth about a workspace nobody changed.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.ui.widgets.point import Point
from src.ui.widgets.provider_modal import ProviderModal
from src.ui.widgets.rollback_choice import RollbackChoice
from src.ui.widgets.rollback_options import ROLLBACK_FILES, action_option, point_option

ROLLBACK_ONLY = 'rollback_only'
FORK = 'fork'
BACK = 'back'

ACTIONS: tuple[tuple[str, str, str], ...] = (
    (ROLLBACK_FILES, 'Roll back, files included', 'the conversation and the workspace, to before this message'),
    (ROLLBACK_ONLY, 'Roll back the conversation only', 'leave the workspace as it is now'),
    (FORK, 'Fork here', 'a new session with the same prefix; this one is left whole'),
)


class RollbackScreen(ProviderModal):
    """Two stages in one box: pick the point, then pick what happens to it."""

    TITLE_TEXT = 'Go back to a message'
    HINT_TEXT = '↑/↓ choose · Enter select · Esc cancel'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False),
    ]

    def __init__(
        self,
        points: list[Point],
        *,
        preselected: str | None = None,
        start_on_actions: bool = False,
    ) -> None:
        super().__init__()
        self._points = points
        self._preselected = preselected
        self._start_on_actions = start_on_actions
        self._stage = 'points'
        self._turn_id: str | None = None

    # --- layout --------------------------------------------------------------

    def compose_body(self) -> ComposeResult:
        if not self._points:
            yield Static(
                Text('This session has no message you can go back to yet.', style='dim'),
                id='rollback-empty',
            )
            return
        options = [point_option(point, index=index) for index, point in enumerate(self._points, start=1)]
        yield OptionList(*options, id='rollback-options')

    def on_mount(self) -> None:
        if not self._points:
            return
        if self._start_on_actions and self._preselected is not None:
            # `/rollback 3`: the list is skipped, the actions of that point are shown.
            self._show_actions(self._preselected)
            return
        options = self.query_one(OptionList)
        if self._preselected is not None:
            ids = [point.checkpoint.turn_id for point in self._points]
            if self._preselected in ids:
                options.highlighted = ids.index(self._preselected)
        options.focus()

    # --- the two stages ------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._stage == 'points':
            self._show_actions(str(event.option.id))
            return
        if event.option.id == BACK:
            self._show_points()
            return
        if self._turn_id is None:
            self.dismiss(None)
            return
        self.dismiss(RollbackChoice(turn_id=self._turn_id, action=str(event.option.id)))

    def _show_actions(self, turn_id: str) -> None:
        """Second stage: what happens to the point that was chosen."""
        self._stage = 'actions'
        self._turn_id = turn_id
        point = next((item for item in self._points if item.checkpoint.turn_id == turn_id), None)
        moment = '' if point is None else point.checkpoint.at[11:19]
        self.set_title(f'Before {moment}: what should happen?')

        options = self.query_one(OptionList)
        options.clear_options()
        for kind, label, detail in ACTIONS:
            options.add_option(action_option(kind, label, detail))
        options.add_option(Option(Text('← back to the list', style='dim'), id=BACK))
        options.highlighted = 0
        options.focus()

        files = 0 if point is None or point.files is None else point.files
        if point is not None and point.files:
            self.set_hint(f'{files} file{"s" if files != 1 else ""} would change · the rollback can be undone')
        else:
            self.set_hint('Nothing to restore in the workspace · Esc cancels')

    def _show_points(self) -> None:
        self._stage = 'points'
        self._turn_id = None
        self.set_title('Go back to a message')
        self.set_hint('↑/↓ choose · Enter select · Esc cancel')
        options = self.query_one(OptionList)
        options.clear_options()
        for index, point in enumerate(self._points, start=1):
            options.add_option(point_option(point, index=index))
        options.highlighted = 0
        options.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = [
    'ACTIONS',
    'BACK',
    'FORK',
    'ROLLBACK_FILES',
    'ROLLBACK_ONLY',
    'Point',
    'RollbackChoice',
    'RollbackScreen',
    'action_option',
    'point_option',
]
