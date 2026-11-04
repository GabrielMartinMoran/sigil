"""Path resolution inside the workspace.

A path outside the workspace is rejected with an explicit error instead of
being resolved silently: the model has to ask for authorization, not guess.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.engine.files.models.file_tool_error import FileToolError


def workspace_root() -> Path:
    """Workspace root: the process working directory."""
    return Path.cwd().resolve()


def resolve(path: str) -> Path:
    """Resolves ``path`` against the workspace and validates that it stays inside."""
    root = workspace_root()
    candidate = Path(path).resolve() if os.path.isabs(path) else (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise FileToolError(
            f'Path {path!r} is outside the workspace ({root}). '
            'Files outside the workspace need explicit authorization from the user.'
        ) from None
    return candidate
