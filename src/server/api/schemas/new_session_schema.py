"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel


class NewSessionSchema(BaseModel):
    """A conversation to start, optionally in a worktree cut first."""

    worktree: str | None = None
