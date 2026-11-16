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
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from src.core.files.models.file_tool_error import FileToolError

_POSIX_TEMP_DIRS = ('/tmp', '/var/tmp')
"""Standard temporary directories of a POSIX system.

``tempfile.gettempdir()`` honours ``TMPDIR``, but a command that writes
``> /tmp/out.txt`` does not: both spellings have to reach the same file or the
allowance only holds while nobody exports ``TMPDIR``.
"""


_execution_workspace: ContextVar[Path | None] = ContextVar('execution_workspace', default=None)
"""Workspace of the execution bound on this task, if there is one.

A `ContextVar` because an execution is a task, not a process: two turns running
at once each resolve their own root, and a task created inside a binding keeps
the root it was born with after the parent leaves (see `execution_workspace`).
"""


@contextmanager
def execution_workspace(root: str | os.PathLike[str]) -> Iterator[Path]:
    """Binds ``root`` as the workspace of this task for the duration of the block.

    The snapshot is absolute and resolved once, on entry: work started with a
    root keeps it, and a workspace selection that happens while the run is in
    flight cannot move the path resolution under a tool that already began. The
    token is reset in ``finally``, so a cancelled or failed execution restores
    the previous binding instead of leaking it to whatever runs next.
    """
    snapshot = Path(os.path.realpath(str(root)))
    token = _execution_workspace.set(snapshot)
    try:
        yield snapshot
    finally:
        _execution_workspace.reset(token)


def workspace_root(fallback: Path | None = None) -> Path:
    """Workspace root of the execution reading it.

    The bound execution wins, then the supplied fallback — the composition's own
    workspace — and only a caller with neither keeps the process working
    directory, which is what a standalone tool outside any composition has.
    """
    selected = _execution_workspace.get()
    if selected is not None:
        return selected
    return Path.cwd().resolve() if fallback is None else fallback


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
