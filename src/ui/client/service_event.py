"""One event read from the service stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    """One event the service published, ready to apply to the screen.

    ``id`` is the service's sequence number, which is the cursor of a reconnect. It
    is ``None`` when the frame carried no usable `id`, and a frame without one does
    not advance the cursor.
    """

    id: int | None
    type: str
    data: dict[str, Any]
