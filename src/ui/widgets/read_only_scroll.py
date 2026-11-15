"""A scroll pane that only scrolls: no caret, no selection."""

from __future__ import annotations

from textual.containers import VerticalScroll


class ReadOnlyScroll(VerticalScroll):
    """A scrolling surface that never asks for the keyboard."""

    can_focus = False
