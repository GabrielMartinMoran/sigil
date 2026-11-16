"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.server.api.schemas.default_entry_schema import DefaultEntrySchema


class ModelDefaultSchema(BaseModel):
    """One default to write, and the file it belongs to.

    ``config`` is required and nullable on purpose: ``null`` clears the entry, which
    is the screen's `d`. Clearing therefore has to be spelled out, and cannot be what
    happens because a client forgot a field.
    """

    target: str | None = None
    """An agent name, or ``null`` for the block a conversation starts on."""
    scope: str = Field(min_length=1)
    """Which of the two files: ``project`` or ``global``."""
    config: DefaultEntrySchema | None
