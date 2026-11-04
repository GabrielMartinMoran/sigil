"""Descubrimiento y merge de la configuración de Sigil.

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
        file_servers, file_errors = load_file(path, env)
        errors.extend(file_errors)
        servers.update(file_servers)

    return LoadedConfig(servers=servers, errors=tuple(errors), sources=tuple(reversed(sources)))
