"""Expansion of ``${VAR}`` and ``${VAR:-default}`` in the config."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from src.engine.mcp.config.constants import ENV_VAR_RE
from src.engine.mcp.config_models.mcp_config_error import MCPConfigError

_ENV_RE = re.compile(ENV_VAR_RE)


def _expand_str(text: str, environ: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group('name')
        default = match.group('default')
        if name in environ:
            return environ[name]
        if default is not None:
            return default
        raise MCPConfigError(
            f'environment variable `{name}` is not set and has no default; use `${{{name}:-fallback}}` to provide one'
        )

    return _ENV_RE.sub(replace, text)


def expand_env_vars(value: Any, environ: Mapping[str, str] | None = None) -> Any:
    """Expands ``${VAR}`` and ``${VAR:-default}`` in strings, lists and dicts."""
    env = os.environ if environ is None else environ
    if isinstance(value, str):
        return _expand_str(value, env)
    if isinstance(value, list):
        return [expand_env_vars(item, env) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_vars(item, env) for key, item in value.items()}
    return value
