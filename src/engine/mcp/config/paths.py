"""Paths of the config files, in order of precedence."""

from __future__ import annotations

import os
from pathlib import Path

from src.engine.mcp.config.constants import CONFIG_DIRNAME, CONFIG_FILENAME, ENV_CONFIG


def config_paths(cwd: str | os.PathLike[str] | None = None) -> list[Path]:
    """Config files in order of precedence (the first one wins)."""
    override = os.environ.get(ENV_CONFIG, '').strip()
    if override:
        return [Path(override).expanduser()]

    base = Path(cwd) if cwd is not None else Path.cwd()
    return [
        base / CONFIG_DIRNAME / CONFIG_FILENAME,
        Path.home() / CONFIG_DIRNAME / CONFIG_FILENAME,
    ]
