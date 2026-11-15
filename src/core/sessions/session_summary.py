"""The SessionSummary value used by the session store."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.sessions.titling import shorten


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """What the picker needs, without the transcript.

    Asking for this used to mean parsing every session file of every workspace —
    396 MB of JSON to paint a list of titles. It is one query now, and it is the
    interface change that matters most outside storage.
    """

    id: str
    workspace: str
    title: str | None
    preview: str
    updated_at: str
    profile: str

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def display_title(self) -> str:
        """The generated title, or the first message cut short."""
        if self.title:
            return self.title
        return shorten(self.preview)
