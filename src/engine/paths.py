"""Filesystem locations owned by Sigil.

Every directory the application writes to is resolved here, so a test can point
the whole application at a temporary tree with a single environment variable
instead of patching ``Path.home`` per module.

``SIGIL_HOME`` replaces the base directory entirely (``~/.sigil`` by default).
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = 'SIGIL_HOME'
ENV_WORKTREES_DIR = 'SIGIL_WORKTREES_DIR'
ENV_THEMES_DIR = 'SIGIL_THEMES_DIR'

DEFAULT_DIRNAME = '.sigil'


def sigil_home(environ: dict[str, str] | None = None) -> Path:
    """Base directory of Sigil's own state.

    Args:
        environ: Environment to read ``SIGIL_HOME`` from (``os.environ`` by
            default). Passing it explicitly keeps callers testable.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_HOME, '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DIRNAME


def sessions_dir(environ: dict[str, str] | None = None) -> Path:
    return sigil_home(environ) / 'sessions'


def roadmaps_dir(environ: dict[str, str] | None = None) -> Path:
    return sigil_home(environ) / 'roadmaps'


def providers_dir(environ: dict[str, str] | None = None) -> Path:
    return sigil_home(environ) / 'providers'


def themes_dir(environ: dict[str, str] | None = None) -> Path:
    """Where the installed themes live, one JSON file each.

    ``SIGIL_THEMES_DIR`` overrides the location, so a theme can be shared between
    machines (or kept in a dotfiles repository) without copying files around.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_THEMES_DIR, '').strip()
    if override:
        return Path(override).expanduser()
    return sigil_home(env) / 'themes'


def worktrees_dir(environ: dict[str, str] | None = None) -> Path:
    """Where `/new --worktree` creates the worktrees.

    ``SIGIL_WORKTREES_DIR`` overrides the location. The default sits under
    ``SIGIL_HOME`` and therefore outside every repository: a worktree created
    *inside* a work tree could be reached by the file tools of another workspace.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_WORKTREES_DIR, '').strip()
    if override:
        return Path(override).expanduser()
    return sigil_home(env) / 'worktrees'
