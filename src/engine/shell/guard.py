"""Anti-self-launch guard: Sigil cannot launch another Sigil instance.

Two layers, because one is not enough:

- ``SELF_GUARD_ENV`` travels in the environment of every child process. If someone
  launches Sigil from inside, the child's ``main.py`` detects it and refuses.
- ``is_self_invocation`` inspects the command before running it, to
  reject it with a clear message instead of letting the child fail.
"""

from __future__ import annotations

import os
import re
import shlex

SELF_GUARD_ENV = 'SIGIL_SELF_GUARD'
"""Environment mark that child processes inherit."""

_WRAPPERS = {'env', 'exec', 'command', 'nohup', 'sudo', 'time', 'uv', 'run'}
"""Programs that only wrap another command (e.g. ``uv run ./main.py``)."""

_PYTHON_RE = re.compile(r'^python(\d+(\.\d+)?)?$')

_SIGIL_PROGRAMS = frozenset({'sigil', 'main.py', 'sigil.py'})


def _first_program(segment: str) -> str | None:
    """First real program of a segment, skipping the wrappers."""
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
    """Detects whether the command tries to launch Sigil (or ``main.py``) itself."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()

    for segment in re.split(r'[;&|]+', ' '.join(tokens)):
        program = _first_program(segment)
        if program in _SIGIL_PROGRAMS:
            return True
    return False
