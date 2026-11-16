"""Shared chrome for the provider modals.

The three provider screens differ in what they show but not in how they are
framed: a bordered box, a title, a hint line and a footer with the keys. Keeping
that in one place means the screens only carry their own behaviour, and the CSS
lives in one block of ``theme.tcss``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ProviderModal(ModalScreen):
    """A centred box with a title, a body and a key hint.

    Subclasses implement ``compose_body`` and set ``TITLE_TEXT``.
    """

    TITLE_TEXT = ''
    HINT_TEXT = 'Enter select · Esc cancel'

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.TITLE_TEXT, id='provider-title'),
            *self.compose_body(),
            Static(self.HINT_TEXT, id='provider-hint'),
            id='provider-box',
        )

    def compose_body(self) -> ComposeResult:
        """The widgets between the title and the hint."""
        raise NotImplementedError

    def set_title(self, text: str) -> None:
        self.query_one('#provider-title', Static).update(text)

    def set_hint(self, text: str) -> None:
        self.query_one('#provider-hint', Static).update(text)
