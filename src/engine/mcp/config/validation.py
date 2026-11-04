"""Validación de una entrada de servidor MCP contra su archivo de origen."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.engine.mcp.config.env_expansion import expand_env_vars
from src.engine.mcp.config_models.constants import SERVER_NAME_RE
from src.engine.mcp.config_models.mcp_config_error import MCPConfigError
from src.engine.mcp.config_models.server_config import MCPServerConfig
from src.engine.mcp.config_models.server_spec import MCPServerSpec


def format_validation(exc: ValidationError) -> str:
    """Mensaje de error legible de un fallo de validación de pydantic."""
    parts: list[str] = []
    for error in exc.errors():
        location = '.'.join(str(item) for item in error['loc'])
        message = error['msg'].removeprefix('Value error, ')
        parts.append(f'{location}: {message}' if location else message)
    return '; '.join(parts)


def build_spec(
    path: Path,
    name: str,
    entry: Any,
    environ: Mapping[str, str],
) -> tuple[MCPServerSpec | None, str | None]:
    """Valida una entrada de ``mcp_servers``. Devuelve ``(spec, error)``."""
    if not isinstance(entry, dict):
        return None, f'{path}: server `{name}` must be a JSON object.'
    if not SERVER_NAME_RE.match(name):
        return None, f'{path}: invalid server name `{name}`; use letters, digits, `-` and `_`.'

    try:
        expanded = expand_env_vars(entry, environ)
    except MCPConfigError as exc:
        return None, f'{path}: server `{name}`: {exc}'

    try:
        config = MCPServerConfig.model_validate(expanded)
    except ValidationError as exc:
        return None, f'{path}: server `{name}`: {format_validation(exc)}'

    spec = MCPServerSpec(name=name, config=config, base_dir=path.parent, source=path)
    cwd = spec.cwd_path
    if cwd is not None and not cwd.is_dir():
        return None, f'{path}: server `{name}`: `cwd` is not an existing directory: {cwd}'
    return spec, None
