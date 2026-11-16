"""The checkout this code was imported from.

Files move between packages; the location of the repository does not. Every
module that needs a file next to the source (the web bundle, the launcher's
entry script) asks here instead of counting ``parents`` from its own depth.
"""

from __future__ import annotations

from pathlib import Path

MARKER = 'pyproject.toml'


def checkout_root(start: Path | None = None) -> Path:
    """Nearest ancestor that holds the repository marker."""
    origin = (start or Path(__file__)).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / MARKER).is_file():
            return candidate
    return origin.parent
