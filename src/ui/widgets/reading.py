"""The surfaces you read, and the one place the keys go.

Everything in this app that merely *shows* something is a scroll container, and a
scroll container is focusable by default. That default is what made a click on the
transcript or the side panel take the keyboard away: the widget gained the focus,
the composer lost it, and the next keystroke went into a widget that only knows
how to scroll.

`ReadOnlyScroll` is the same container with the focus taken out of it. A click
cannot focus it and `Tab` cannot reach it, so the composer stays the only keyboard
surface in the app. What must keep working — and does — is the mouse wheel, text
selection and the scroll itself.

`current_pane` and `scroll_pane` are the keyboard half of the same idea: the keys
that scroll the conversation belong to whoever has the keyboard, so they are
handled by the composer (and by the question panel while it is open) and applied
here, to whichever pane is the one on screen.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen

PANES = (
    ('#chat', '#chat'),
    ('#agent', '#agent-chat'),
    ('#debug', '#debug-scroll'),
)
"""``(holder, pane)`` for the three views that share the space of the conversation.

The holder is what says which one is on screen: the pane's own ``display`` stays
true while its container is hidden.
"""

PAGE_OVERLAP = 2
"""Lines a page keeps in view.

A page that moves the full height leaves the reader with nothing they were just
reading, and the eye has to find the place again. Two lines of overlap is the
cheapest fix that does not make paging feel like a nudge.
"""

PAGE_FALLBACK = 10
"""Lines a page moves when the pane has no measured height yet (before the first layout)."""

SCROLL_UP_KEY = 'ctrl+up'
SCROLL_DOWN_KEY = 'ctrl+down'
"""The pair that reads the conversation without leaving what you are doing.

`ctrl+↑` / `ctrl+↓` rather than `pgup` / `pgdn` for the small step, because both
surfaces that own the keyboard handle it: the composer and the question panel.
"""

SCROLL_LINES = 5
"""Lines one `ctrl+↑` / `ctrl+↓` moves: enough to read back, not a full page."""


class ReadOnlyScroll(VerticalScroll):
    """A scrolling surface that never asks for the keyboard."""

    can_focus = False


def current_pane(screen: Screen) -> VerticalScroll | None:
    """The conversation pane on screen, or ``None`` when there is none."""
    for holder_selector, pane_selector in PANES:
        try:
            holder = screen.query_one(holder_selector)
            pane = screen.query_one(pane_selector, VerticalScroll)
        except NoMatches:
            continue
        if holder.display:
            return pane
    return None


def scroll_pane(pane: VerticalScroll, lines: int) -> None:
    """Moves the pane ``lines`` rows (negative is up), releasing the bottom anchor.

    Reading history must not fight the transcript: while the view is anchored to
    the bottom, every message that arrives drags it down again. Going up lets go of
    the anchor; going down does not re-anchor it, because the end of the transcript
    is where anchoring comes back (Textual re-anchors at the bottom).
    """
    if lines == 0:
        return
    target = max(0, int(pane.scroll_offset.y) + lines)
    pane.scroll_to(y=target, animate=False)
    if lines < 0:
        pane.release_anchor()


def page_lines(pane: VerticalScroll) -> int:
    """How far a page moves: the visible height, less the overlap.

    `size` is already the content area, so the frame around the pane is not
    counted twice.
    """
    height = pane.size.height
    if height <= PAGE_OVERLAP:
        return PAGE_FALLBACK
    return height - PAGE_OVERLAP
