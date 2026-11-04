"""Ejecución de scripts empaquetados dentro de un skill.

La extensión manda sobre el shebang para los lenguajes conocidos: así un ``.py``
usa el intérprete de Sigil (con sus dependencias) aunque el script declare
``#!/usr/bin/env python3``.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from src.engine.skills.models.skill_resource_error import SkillResourceError

_KNOWN_INTERPRETERS = {
    '.py': lambda: [sys.executable],
    '.sh': lambda: ['/bin/bash'],
    '.bash': lambda: ['/bin/bash'],
    '.js': lambda: ['node'],
    '.mjs': lambda: ['node'],
    '.cjs': lambda: ['node'],
    '.rb': lambda: ['ruby'],
}


def _shebang(path: Path) -> list[str]:
    """Intérprete declarado en la primera línea, si hay."""
    try:
        first_line = path.open('rb').readline(256).decode('utf-8', errors='replace').strip()
    except OSError:
        return []
    if not first_line.startswith('#!'):
        return []
    return shlex.split(first_line[2:].strip())


def interpreter_for(path: Path) -> list[str]:
    """Elige cómo ejecutar un script según extensión, shebang o permisos.

    Una lista vacía significa "ejecutalo directamente": el archivo es ejecutable
    y su shebang se encarga.
    """
    known = _KNOWN_INTERPRETERS.get(path.suffix.lower())
    if known is not None:
        return known()

    shebang = _shebang(path)
    if shebang:
        return shebang

    if os.access(path, os.X_OK):
        return []

    raise SkillResourceError(
        f'Cannot determine how to run {path.name!r}. Use a .py/.sh/.js/.rb script, '
        'add a shebang and make it executable, or run it through the `run` tool.'
    )
