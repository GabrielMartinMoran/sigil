"""Descubrimiento, merge y validación de ``.sigil/config.json``.

Rutas, en orden de precedencia (la primera gana):

1. ``<cwd>/.sigil/config.json`` — configuración del proyecto.
2. ``~/.sigil/config.json``     — configuración global del usuario.

``SIGIL_CONFIG`` reemplaza ambas por una ruta explícita. El merge es por
servidor: una entrada del proyecto con el mismo nombre reemplaza entera a la
global (no se mezclan campos, para que el resultado sea predecible).

Los errores se acumulan en lugar de abortar: un servidor mal escrito no impide
usar el resto de la configuración.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.engine.mcp.config_models import (
    SERVER_NAME_RE,
    MCPConfigError,
    MCPServerConfig,
    MCPServerSpec,
)

CONFIG_DIRNAME = '.sigil'
CONFIG_FILENAME = 'config.json'
ENV_CONFIG = 'SIGIL_CONFIG'

_TOP_LEVEL_KEYS = {'mcp_servers'}
_ENV_RE = re.compile(r'\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}')


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """Resultado de leer la configuración: servidores válidos y errores."""

    servers: dict[str, MCPServerSpec]
    errors: tuple[str, ...] = ()
    sources: tuple[Path, ...] = ()

    def __len__(self) -> int:
        return len(self.servers)

    def enabled(self) -> dict[str, MCPServerSpec]:
        return {name: spec for name, spec in self.servers.items() if spec.config.enabled}


# --- expansión de variables de entorno ---------------------------------------


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
    """Expande ``${VAR}`` y ``${VAR:-default}`` en strings, listas y dicts."""
    env = os.environ if environ is None else environ
    if isinstance(value, str):
        return _expand_str(value, env)
    if isinstance(value, list):
        return [expand_env_vars(item, env) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_vars(item, env) for key, item in value.items()}
    return value


# --- rutas -------------------------------------------------------------------


def config_paths(cwd: str | os.PathLike[str] | None = None) -> list[Path]:
    """Archivos de configuración en orden de precedencia (el primero gana)."""
    override = os.environ.get(ENV_CONFIG, '').strip()
    if override:
        return [Path(override).expanduser()]

    base = Path(cwd) if cwd is not None else Path.cwd()
    return [
        base / CONFIG_DIRNAME / CONFIG_FILENAME,
        Path.home() / CONFIG_DIRNAME / CONFIG_FILENAME,
    ]


# --- carga -------------------------------------------------------------------


def _format_validation(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = '.'.join(str(item) for item in error['loc'])
        message = error['msg'].removeprefix('Value error, ')
        parts.append(f'{location}: {message}' if location else message)
    return '; '.join(parts)


def _build_spec(
    path: Path,
    name: str,
    entry: Any,
    environ: Mapping[str, str],
) -> tuple[MCPServerSpec | None, str | None]:
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
        return None, f'{path}: server `{name}`: {_format_validation(exc)}'

    spec = MCPServerSpec(name=name, config=config, base_dir=path.parent, source=path)
    cwd = spec.cwd_path
    if cwd is not None and not cwd.is_dir():
        return None, f'{path}: server `{name}`: `cwd` is not an existing directory: {cwd}'
    return spec, None


def _load_file(path: Path, environ: Mapping[str, str]) -> tuple[dict[str, MCPServerSpec], list[str]]:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except OSError as exc:
        return {}, [f'{path}: cannot read config: {exc}']
    except json.JSONDecodeError as exc:
        return {}, [f'{path}: invalid JSON: {exc}']

    if not isinstance(raw, dict):
        return {}, [f'{path}: the config root must be a JSON object.']

    errors: list[str] = []
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if 'mcpServers' in unknown:
        unknown.discard('mcpServers')
        errors.append(f'{path}: `mcpServers` is not supported; use snake_case `mcp_servers`.')
    if unknown:
        supported = ', '.join(sorted(_TOP_LEVEL_KEYS))
        errors.append(f'{path}: unknown top-level keys: {", ".join(sorted(unknown))}. Supported: {supported}.')

    entries = raw.get('mcp_servers', {})
    if not isinstance(entries, dict):
        errors.append(f'{path}: `mcp_servers` must be a JSON object mapping server names to settings.')
        return {}, errors

    servers: dict[str, MCPServerSpec] = {}
    for name, entry in entries.items():
        spec, error = _build_spec(path, name, entry, environ)
        if error is not None:
            errors.append(error)
        elif spec is not None:
            servers[name] = spec
    return servers, errors


def load_config(
    paths: Sequence[Path] | None = None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> LoadedConfig:
    """Lee y mergea la configuración de Sigil.

    Args:
        paths: Archivos a leer, en orden de precedencia. Por defecto se usan
            ``config_paths()``.
        cwd: Directorio base para descubrir la config del proyecto.
        environ: Entorno para expandir ``${VAR}`` (por defecto ``os.environ``).
    """
    env = os.environ if environ is None else environ
    explicit = paths is None and bool(os.environ.get(ENV_CONFIG, '').strip())
    candidates = list(paths) if paths is not None else config_paths(cwd=cwd)

    errors: list[str] = []
    servers: dict[str, MCPServerSpec] = {}
    sources: list[Path] = []

    # De menor a mayor precedencia: lo último leído pisa lo anterior.
    for path in reversed(candidates):
        if not path.is_file():
            if explicit:
                errors.append(f'Config file not found: {path}')
            continue
        sources.append(path)
        file_servers, file_errors = _load_file(path, env)
        errors.extend(file_errors)
        servers.update(file_servers)

    return LoadedConfig(servers=servers, errors=tuple(errors), sources=tuple(reversed(sources)))
