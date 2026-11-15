"""The AcceptedInput value of the accepted-input queue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.core.storage.stored_image import StoredImage

InputKind = Literal['turn', 'steer']


InputState = Literal['accepted', 'claimed', 'delivered', 'finished', 'stopped', 'interrupted']


@dataclass(frozen=True, slots=True)
class AcceptedInput:
    session_id: str
    request_id: str
    sequence: int
    text: str
    images: tuple[StoredImage, ...]
    kind: InputKind
    state: InputState
    attempt_id: str | None
    library_run_id: str | None
    created_at: str
    updated_at: str
    origin: dict[str, Any] | None = None
