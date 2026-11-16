"""One image as the client sent it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IncomingImage:
    """One image as the client sent it: the type it declared and base64 bytes.

    Nothing here is ever a path. A client sends the picture itself, never a filename
    the server would open, and the declared type is only a claim: the bytes are
    decoded and re-encoded before they are trusted.
    """

    media_type: str
    data: str
