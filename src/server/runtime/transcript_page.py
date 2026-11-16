"""One window of a projected transcript."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TranscriptPage:
    """One window of a projected transcript, and the cursor that precedes it.

    ``items`` are projected items, unchanged: a page never rewrites one. The item's
    ``index`` is the message it was built from — the absolute point a click maps to
    its rollback — and it travels as it is, so a window cannot renumber a
    conversation.

    ``first_index`` is the absolute position of the first item in the whole
    transcript, and it doubles as the ``before`` cursor for the chunk just above
    this page. ``total`` counts transcript items only: a row waiting in the queue is
    not one, so it neither enters the count nor moves a cursor.
    """

    items: list[dict[str, Any]]
    first_index: int
    has_more: bool
    total: int
