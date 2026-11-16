"""A range to remove from image marker text."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Deletion:
    """The range a block deletion removes, in document offsets."""

    start: int
    end: int
