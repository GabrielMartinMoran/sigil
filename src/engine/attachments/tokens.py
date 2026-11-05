"""The `[Image #n]` marker inside the message text, as text and as a unit.

An image cannot be pasted into a terminal the way it is pasted into a browser, so
the composer keeps the bytes aside and puts a marker in the text where the cursor
was. That marker is the message: it is what the model reads in the prompt, and it
is what the user edits around.

The marker is a *block*, and that is the whole point of this module. Deleting a
screenshot character by character leaves `[Imag` behind, the send goes out with a
broken reference, and the image still travels — the model gets a picture nobody
asked to attach. `block_deletion` makes the marker behave like the thing it
represents: one backspace when the cursor is after it, one delete when it is
before, one change when it is selected.

The number is not decoration either. `[Image #2]` that survives the deletion of
`[Image #1]` is a reference to nothing, so `renumber` runs on every edit and keeps
the text and the attachments in the same order.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

TOKEN_PATTERN = re.compile(r'\[Image #(\d+)\]')
"""The marker, with its number captured."""

Location = tuple[int, int]
"""``(row, column)``, the pair the `TextArea` speaks in."""

Selection = tuple[Location, Location]
"""Ordered ``(start, end)``, as `TextArea.selection` returns it."""


def token(number: int) -> str:
    """The marker for the ``number``-th image of a message (1-based)."""
    return f'[Image #{number}]'


def spans(text: str) -> list[TokenSpan]:
    """Every marker in ``text``, left to right, with document offsets."""
    return [TokenSpan(match.start(), match.end(), int(match.group(1))) for match in TOKEN_PATTERN.finditer(text)]


@dataclass(frozen=True, slots=True)
class TokenSpan:
    """A marker found in the text."""

    start: int
    """Offset of its first character in the whole document."""

    end: int
    """Offset just past its last character."""

    number: int

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end

    @property
    def text(self) -> str:
        return token(self.number)


@dataclass(frozen=True, slots=True)
class Deletion:
    """The range a block deletion removes, in document offsets."""

    start: int
    end: int


def location(text: str, offset: int) -> Location:
    """``offset`` in the whole document as a ``(row, column)`` pair."""
    before = text[:offset]
    row = before.count('\n')
    return row, offset - (before.rfind('\n') + 1)


def offset(text: str, position: Location) -> int:
    """The inverse of `location`: a ``(row, column)`` pair as a document offset."""
    row, column = position
    if row == 0:
        return column
    lines = text.split('\n')
    return sum(len(line) + 1 for line in lines[:row]) + column


def image_at(text: str, position: Location) -> TokenSpan | None:
    """The marker the cursor is *inside*.

    A cursor sitting just after the last character of a marker is not inside it:
    that is the position a backspace acts from, and it is handled by
    `block_deletion` instead.
    """
    cursor = offset(text, position)
    for span in spans(text):
        if span.contains(cursor):
            return span
    return None


def block_deletion(text: str, selection: Selection, *, backspace: bool) -> Deletion | None:
    """The range a delete key removes, widened to the whole marker when there is one.

    Args:
        text: The composer document.
        selection: Current ``(start, end)`` selection.
        backspace: ``True`` for `backspace` (delete to the left), ``False`` for
            `delete` (delete to the right).

    Returns:
        The range to delete, or ``None`` when this key is an ordinary edit and the
        `TextArea` should handle it.
    """
    start, end = selection
    if start != end:
        return _widen_selection(text, start, end)
    cursor = offset(text, start)
    if backspace:
        for span in spans(text):
            # Only the character immediately to the left counts: a marker two
            # words back is not what this keypress is about.
            if span.end == cursor:
                return Deletion(span.start, cursor)
        return None
    for span in spans(text):
        if span.start == cursor:
            return Deletion(cursor, span.end)
    return None


def _widen_selection(text: str, start: Location, end: Location) -> Deletion | None:
    """Expands a selection that cuts through a marker so no fragment survives."""
    first = offset(text, start)
    last = offset(text, end)
    widened_start = first
    widened_end = last
    for span in spans(text):
        if span.start >= last or span.end <= first:
            continue
        widened_start = min(widened_start, span.start)
        widened_end = max(widened_end, span.end)
    if (widened_start, widened_end) == (first, last):
        return None
    return Deletion(widened_start, widened_end)


def numbers_in(text: str) -> list[int]:
    """Attachment numbers mentioned in ``text``, in order, without repetitions."""
    seen: list[int] = []
    for span in spans(text):
        if span.number not in seen:
            seen.append(span.number)
    return seen


def renumber(text: str, keep: Sequence[int]) -> str:
    """Rewrites the markers of ``keep`` so they read 1..n, and drops the rest.

    Args:
        text: The document to rewrite.
        keep: Attachment numbers still attached, in the order the text mentions
            them.

    Returns:
        The rewritten document. A marker whose image is gone is removed together
        with one adjacent space, so the sentence does not keep a hole.
    """
    found = spans(text)
    if not found:
        return text
    mapping = {old: new for new, old in enumerate(keep, start=1)}
    pieces: list[str] = []
    position = 0
    for span in found:
        pieces.append(text[position : span.start])
        replacement = mapping.get(span.number)
        if replacement is not None:
            pieces.append(token(replacement))
            position = span.end
            continue
        # The image went away: the reference goes with it, and one of the two
        # spaces that separated it, so "ver [Image #1] esto" reads "ver esto".
        position = span.end
        if text[position : position + 1] == ' ':
            position += 1
        elif pieces and pieces[-1].endswith(' '):
            pieces[-1] = pieces[-1][:-1]
    pieces.append(text[position:])
    return ''.join(pieces)


def render_inline(text: str) -> str:
    """``text`` with its markers spelled out for a reader beyond the composer.

    Used by the chat and by the session preview, where a bare `[Image #1]` is
    accurate but reads as if the user typed it.
    """
    return TOKEN_PATTERN.sub(lambda match: f'[Image #{match.group(1)}]', text)


def remap(text: str, mapping: dict[int, int]) -> str:
    """Replaces each marker's number according to ``mapping``.

    Unlike `renumber`, it takes an explicit map and leaves the markers missing from
    it untouched. That is what putting several messages back in the composer needs:
    each one carries its own numbering starting at one, so joining two of them
    means giving the second one's images the numbers that follow the first's.
    """
    if not mapping:
        return text
    return TOKEN_PATTERN.sub(
        lambda match: token(mapping.get(int(match.group(1)), int(match.group(1)))),
        text,
    )
