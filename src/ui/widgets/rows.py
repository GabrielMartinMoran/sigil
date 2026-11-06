"""Composing a line of a fixed width: something on the left, something flush right.

A row of the transcript shares its width between what it says and what it concluded —
`write tests/test_roadmap.py` on one side, `42 lines · 1.2 KB` on the other. Rich has
`Table` and a console for that, and both bring their own layout, colors and padding
to a widget that already has all three. This is the two-hundred-line abstraction's
smallest useful slice: measure, clip, pad.

The width is passed in and never guessed. A widget cannot know its own final width
while it is being composed, and the transcript is full of things that are wider than
the screen: the rule is that the caller paints from `size.width` and repaints on
resize, so the right edge stays flush when the terminal changes.
"""

from __future__ import annotations

from rich.text import Text

GAP = 2
"""Cells kept between the two halves when they do not touch."""

MIN_LEFT = 12
"""Below this, the row is a fragment and the right half is in the way.

It is the width of `write tests/t…`: enough to tell one call from another. Under it,
the right half is dropped and the left one gets the whole row — a clipped summary is
worse than no summary, because the truncated half reads like the whole answer.
"""


def fit(left: Text, right: Text | None, width: int) -> Text:
    """``left`` and ``right`` on one line of ``width`` cells, the right edge flush.

    When both fit, ``left`` is padded so ``right`` ends at the last column. When they
    do not, ``left`` is clipped and ``right`` is kept whole while there is room for a
    readable left; when there is not, ``right`` is dropped.
    """
    if width <= 0:
        return left
    if right is None or not right.cell_len:
        return clipped(left, width)
    room = width - right.cell_len - GAP
    if room < MIN_LEFT:
        return clipped(left, width)
    row = clipped(left, room)
    row.append(' ' * max(0, width - row.cell_len - right.cell_len))
    row.append_text(right)
    return row


def clipped(text: Text, width: int) -> Text:
    """A copy of ``text`` cut to ``width`` cells, ending in `…` when it had to cut."""
    if width <= 0 or text.cell_len <= width:
        return text.copy()
    clipped_text = text.copy()
    clipped_text.truncate(width, overflow='ellipsis')
    return clipped_text
