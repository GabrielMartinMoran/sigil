"""The StoredImage value of the accepted-input queue."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoredImage:
    data: bytes
    media_type: str
    image_id: str
