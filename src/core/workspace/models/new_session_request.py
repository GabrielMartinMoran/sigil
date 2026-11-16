"""Arguments for a new session."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NewSessionRequest:
    """What `/new` has to do."""

    worktree: str | None = None
