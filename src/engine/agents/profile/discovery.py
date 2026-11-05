"""Where agent profiles are looked up.

Three roots, in order of precedence (the first one wins when two profiles share
a name):

1. ``<cwd>/.sigil/agents``: agents that belong to the project.
2. ``<sigil home>/agents``: ``~/.sigil/agents`` by default, unless
   ``SIGIL_HOME`` points elsewhere.
3. The package directory: the agents Sigil ships with.

A project profile shadows a global one, and either of them shadows a built-in of
the same name: the shipped set is a starting point, not a floor.

Profiles are flat ``.md`` files. A name starting with ``_`` or ``.`` is skipped,
so a notes file or an editor's swap file next to the profiles is not parsed as
one.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.engine.paths import sigil_home

AGENTS_DIRNAME = '.sigil/agents'
BUILTIN_DIRNAME = Path(__file__).resolve().parent / 'builtin'
PROFILE_SUFFIX = '.md'


def default_roots(cwd: str | os.PathLike[str] | None = None) -> list[tuple[str, Path]]:
    """Agent roots as ``(source, path)``, highest precedence first."""
    base = Path(cwd) if cwd is not None else Path.cwd()
    return [
        ('project', base / AGENTS_DIRNAME),
        ('global', sigil_home() / 'agents'),
        ('builtin', BUILTIN_DIRNAME),
    ]


def is_profile_file(path: Path) -> bool:
    """Whether ``path`` is a candidate profile: a visible ``.md`` file."""
    return path.is_file() and path.suffix == PROFILE_SUFFIX and not path.name.startswith(('_', '.'))
