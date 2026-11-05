"""Derivation and validation of the names a new worktree gets.

A slug is the only thing the user types, so it must be safe as a directory name
and as a branch name at once, and it must not need escaping anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.engine.workspace.models.workspace_error import WorkspaceError

MAX_SLUG_LENGTH = 40
"""Long enough to be descriptive, short enough for the path next to the repo name."""

DEFAULT_BRANCH_PREFIX = 'sigil/'
"""Branches created by Sigil are namespaced so they do not collide with human ones."""

_SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')

DEFAULT_WORKTREES_DIRNAME = Path('.sigil') / 'worktrees'
"""`~/.sigil/worktrees`: outside every work tree, so a workspace cannot reach another."""


def validate_slug(slug: str) -> str:
    """Normalizes and validates a slug. Returns it trimmed and lowercased."""
    value = slug.strip().lower()
    if not value:
        raise WorkspaceError('A worktree needs a name: `/new --worktree <name>`.')
    if len(value) > MAX_SLUG_LENGTH:
        raise WorkspaceError(f'Worktree name too long ({len(value)}): use at most {MAX_SLUG_LENGTH} characters.')
    if not _SLUG_RE.match(value):
        raise WorkspaceError(
            f'Invalid worktree name {slug.strip()!r}: use lowercase letters, digits and single hyphens '
            '(for example `fix-login`).'
        )
    return value


def branch_for(slug: str, prefix: str = DEFAULT_BRANCH_PREFIX) -> str:
    """Branch a new worktree is created on."""
    return f'{prefix}{validate_slug(slug)}'


def worktree_path(base_dir: Path, repository: str, slug: str) -> Path:
    """Destination of the worktree: ``<base>/<repository>/<slug>``.

    The repository name is kept as a folder so worktrees of different
    repositories never share a namespace.
    """
    return base_dir / repository / validate_slug(slug)
