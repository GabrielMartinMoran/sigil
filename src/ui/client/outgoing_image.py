"""An image travelling out with a message."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutgoingImage:
    """One picture of a message, in the order the text marks it.

    ``media_type`` is what the client claims the bytes are and ``data`` is their
    base64 payload, exactly the shape `/api/turn` accepts. A position is not a
    field: the list's order is the ``[Image #n]`` number, which is why
    `TurnRequest` keeps a tuple.
    """

    media_type: str
    data: str
