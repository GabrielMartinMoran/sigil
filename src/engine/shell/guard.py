"""Guarda anti-autoarranque: Sigil no puede lanzar otra instancia de Sigil.

Dos capas, porque una sola no alcanza:

- ``SELF_GUARD_ENV`` viaja en el entorno de cada proceso hijo. Si alguien
  arranca Sigil desde adentro, el ``main.py`` del hijo lo detecta y se niega.
- ``is_self_invocation`` inspecciona el comando antes de ejecutarlo, para
  rechazarlo con un mensaje claro en vez de dejar que el hijo falle.
"""

from __future__ import annotations

import os
import re
import shlex

SELF_GUARD_ENV = 'SIGIL_SELF_GUARD'
"""Marca de entorno que heredan los procesos hijos."""

_WRAPPERS = {'env', 'exec', 'command', 'nohup', 'sudo', 'time', 'uv', 'run'}
"""Programas que sólo envuelven a otro comando (p. ej. ``uv run ./main.py``)."""

_PYTHON_RE = re.compile(r'^python(\d+(\.\d+)?)?$')

_SIGIL_PROGRAMS = frozenset({'sigil', 'main.py', 'sigil.py'})


def _first_program(segment: str) -> str | None:
    """Primer programa real de un segmento, salteando los wrappers."""
    words = segment.split()
    index = 0
    while index < len(words):
        name = os.path.basename(words[index])
        if name in _WRAPPERS or _PYTHON_RE.match(name):
            index += 1
            continue
        break
    return os.path.basename(words[index]) if index < len(words) else None


def is_self_invocation(cmd: str) -> bool:
    """Detecta si el comando intenta arrancar Sigil (u ``main.py``) a sí mismo."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()

    for segment in re.split(r'[;&|]+', ' '.join(tokens)):
        program = _first_program(segment)
        if program in _SIGIL_PROGRAMS:
            return True
    return False
