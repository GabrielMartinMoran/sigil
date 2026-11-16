"""One command the palette offers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaletteChoice:
    name: str
    worktree: bool = False
    args: str = ''
