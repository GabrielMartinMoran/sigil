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
ENV_SERVICE_DIR = 'SIGIL_SERVICE_DIR'

DEFAULT_DIRNAME = '.sigil'

SERVICE_DIRNAME = 'service'
"""Subdirectory of ``SIGIL_HOME`` where a running service says where it listens.

Separate from ``SIGIL_HOME`` itself on purpose: the service directory is created
``0700`` and removed from nothing else, while ``SIGIL_HOME`` is shared with the
database and the sessions.
"""

ENDPOINT_FILENAME = 'endpoint.json'
"""The file a client reads to find the running service, one per data directory."""


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


def service_dir(environ: dict[str, str] | None = None) -> Path:
    """Where a running service publishes the endpoint a client reads.

    ``SIGIL_SERVICE_DIR`` overrides the location, for the same reason the other
    directories have an override: a test, or a deployment, points the whole tree
    somewhere else instead of patching a module constant. The directory is created
    ``0700`` by the publisher, because the file inside it carries a token.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_SERVICE_DIR, '').strip()
    if override:
        return Path(override).expanduser()
    return sigil_home(env) / SERVICE_DIRNAME


def endpoint_file(environ: dict[str, str] | None = None) -> Path:
    """The rendezvous file of the service that owns this data directory."""
    return service_dir(environ) / ENDPOINT_FILENAME
