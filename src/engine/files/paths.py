"""Resolución de rutas dentro del workspace.

Una ruta fuera del workspace se rechaza con un error explícito en vez de
resolverse en silencio: el modelo tiene que pedir autorización, no adivinar.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.engine.files.models.file_tool_error import FileToolError


def workspace_root() -> Path:
    """Raíz del workspace: el directorio de trabajo del proceso."""
    return Path.cwd().resolve()


def resolve(path: str) -> Path:
    """Resuelve ``path`` contra el workspace y valida que quede adentro."""
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
