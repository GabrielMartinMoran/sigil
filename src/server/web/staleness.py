"""Whether the built web bundle matches its sources."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Staleness:
    """Why the bundle does not match the sources, in the terms a user acts on."""

    stale: bool
    reason: str = ''
    source_hash: str = ''
    built_hash: str | None = None

    def message(self) -> str:
        """One sentence for a terminal or a log line. Empty when fresh."""
        if not self.stale:
            return ''
        return f'{self.reason}. Run `npm run build` in web/ (or `python -m src.server.webbuild --build`).'
