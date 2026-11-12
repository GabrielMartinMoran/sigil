"""Path resolution inside the workspace and inside the OS temporary directory.

A path outside those two trees is rejected with an explicit error instead of
being resolved silently: the model has to ask for authorization, not guess.

The temporary directory is allowed because scratch files are what it is for. A
command writes one, the model reads it back, and refusing the path would turn a
one-line workflow into a round trip through the shell that the shell would win
anyway.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.engine.files.models.file_tool_error import FileToolError

_POSIX_TEMP_DIRS = ('/tmp', '/var/tmp')
"""Standard temporary directories of a POSIX system.

``tempfile.gettempdir()`` honours ``TMPDIR``, but a command that writes
``> /tmp/out.txt`` does not: both spellings have to reach the same file or the
allowance only holds while nobody exports ``TMPDIR``.
"""


def workspace_root() -> Path:
    """Workspace root: the process working directory."""
    return Path.cwd().resolve()


def temp_dirs() -> tuple[Path, ...]:
    """The temporary directories of this session, resolved and without duplicates.

    The list is read on every call instead of once at import, so pointing
    ``TMPDIR`` at a sandbox takes effect for the tools. Resolving also makes a
    symlinked temp directory (macOS: ``/var`` → ``/private/var``) compare equal
    to the paths the tools actually receive. A directory that cannot be resolved
    is skipped: it is not a place a file can be written to.
    """
    candidates = [tempfile.gettempdir()]
    if os.name != 'nt':
        candidates.extend(_POSIX_TEMP_DIRS)

    resolved: dict[Path, None] = {}
    for candidate in candidates:
        try:
            resolved.setdefault(Path(candidate).expanduser().resolve(), None)
        except OSError:
            continue
    return tuple(resolved)


def allowed_roots(root: Path | None = None) -> tuple[Path, ...]:
    """The trees the file tools may work in: the workspace and the temp directories."""
    return (workspace_root() if root is None else root, *temp_dirs())


def is_allowed(path: Path, root: Path | None = None) -> bool:
    """Whether ``path`` (already resolved) sits inside one of the allowed trees."""
    return any(path.is_relative_to(base) for base in allowed_roots(root))


def resolve(path: str, *, allow_external: bool = False, root: Path | None = None) -> Path:
    """Resolves ``path`` and validates that it stays inside an allowed tree.

    A relative path is resolved against the workspace; an absolute one is used
    as given. Symlinks are followed *before* the check, so a link inside an
    allowed tree cannot be used to reach a file outside it.
    """
    root = workspace_root() if root is None else root.resolve()
    candidate = Path(path).resolve() if os.path.isabs(path) else (root / path).resolve()
    if not allow_external and not is_allowed(candidate, root):
        raise FileToolError(
            f'Path {path!r} is outside the workspace ({root}) and outside the temporary directory. '
            'Files outside them need explicit authorization from the user.'
        )
    return candidate
