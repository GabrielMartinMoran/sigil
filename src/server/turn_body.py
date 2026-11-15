"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.server.image_body import ImageBody


class TurnBody(BaseModel):
    """A message from the composer: text, an optional request id, ordered images.

    ``images[0]`` is ``[Image #1]`` in the text; the position is the number, and it
    is part of what a retry has to match to be treated as the same message.
    """

    text: str = Field(min_length=1)
    request_id: str | None = None
    """Client-generated UUIDv4, reused by a retry of the same deliberate send."""
    images: list[ImageBody] = Field(default_factory=list)
    """Pictures in the order the text refers to them; bounded by the runtime."""
