"""The ImageOccurrence value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageOccurrence:
    """One image part, where it lives and when it was loaded."""

    identifier: str
    turn: str | None
    turn_index: int
    tokens: int
    message_index: int
    part_index: int
    item_index: int

    @property
    def position(self) -> tuple[int, int, int]:
        return (self.message_index, self.part_index, self.item_index)
