"""Discovery and merge of the Sigil config.

Paths, in order of precedence (the first one wins):

1. ``<cwd>/.sigil/config.json``: project config.
2. ``~/.sigil/config.json``: user global config.

``SIGIL_CONFIG`` replaces both with an explicit path. The merge is per
server: a project entry with the same name replaces the global one
entirely (fields are not mixed, so the result stays predictable).

Errors accumulate instead of aborting: a badly written server does not prevent
using the rest of the config.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from src.engine.mcp.config.constants import ENV_CONFIG
from src.engine.mcp.config.file_loader import load_file
from src.engine.mcp.config.loaded_config import LoadedConfig
from src.engine.mcp.config.paths import config_paths
from src.engine.mcp.config_models.server_spec import MCPServerSpec


def load_config(
    paths: Sequence[Path] | None = None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> LoadedConfig:
    """Reads and merges the Sigil config.

    Args:
        paths: Files to read, in order of precedence. ``config_paths()`` is used
            by default.
        cwd: Base directory to discover the project config.
        environ: Environment to expand ``${VAR}`` (``os.environ`` by default).
    """
    env = os.environ if environ is None else environ
    explicit = paths is None and bool(os.environ.get(ENV_CONFIG, '').strip())
    candidates = list(paths) if paths is not None else config_paths(cwd=cwd)

    errors: list[str] = []
    servers: dict[str, MCPServerSpec] = {}
    sources: list[Path] = []

    # From lowest to highest precedence: the last one read overrides the previous one.
    for path in reversed(candidates):
        if not path.is_file():
            if explicit:
                errors.append(f'Config file not found: {path}')
            continue
        sources.append(path)
        file_servers, file_errors = load_file(path, env)
        errors.extend(file_errors)
        servers.update(file_servers)

    return LoadedConfig(servers=servers, errors=tuple(errors), sources=tuple(reversed(sources)))
