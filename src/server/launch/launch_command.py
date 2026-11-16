"""How to start a server, as data a test can aim elsewhere."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LaunchCommand:
    """How to start a server, as data so a test can aim it at a process double."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    """Variables *added* to this process's own; ``None`` inherits it unchanged.

    Additions, never a replacement. `SIGIL_SERVICE_DIR` and `SIGIL_SELF_GUARD` are
    inherited facts, and a launcher able to drop them would be a launcher able to
    start a server somewhere other than the one it is looking at.
    """
