"""One frame of the server-sent event stream."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SseFrame:
    """One event as the SSE protocol defines it, before any JSON is read.

    ``data`` is the joined `data:` lines and nothing is interpreted: the protocol
    is a text format and this type says so.
    """

    id: str | None = None
    event: str | None = None
    data: str = ''
