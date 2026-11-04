"""Servidor MCP validado, con el contexto del archivo que lo declaró."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.engine.mcp.config_models.server_config import MCPServerConfig


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    """Servidor validado, con el contexto del archivo que lo declaró."""

    name: str
    config: MCPServerConfig
    base_dir: Path
    source: Path

    @property
    def transport_kind(self) -> Literal['stdio', 'http']:
        return 'stdio' if self.config.command else 'http'

    @property
    def tool_prefix(self) -> str:
        """Prefijo de las tools; ``None`` usa el nombre del server, ``''`` no prefija."""
        prefix = self.config.prefix
        return self.name if prefix is None else prefix

    @property
    def cwd_path(self) -> Path | None:
        """``cwd`` resuelto: ``~`` expandido y rutas relativas al archivo de config."""
        raw = self.config.cwd
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        return path.resolve()
