"""Un archivo de instrucciones encontrado en el árbol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    """Un archivo de instrucciones encontrado en el árbol."""

    path: Path
    relative: str
    content: str
    truncated: bool
