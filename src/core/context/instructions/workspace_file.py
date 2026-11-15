"""An instruction file found in the tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    """An instruction file found in the tree."""

    path: Path
    relative: str
    content: str
    truncated: bool
