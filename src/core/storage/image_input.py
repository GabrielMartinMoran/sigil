"""The ImageInput value of the accepted-input queue."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageInput:
    data: bytes
    media_type: str
