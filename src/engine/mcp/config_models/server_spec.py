"""Validated MCP server, with the context of the file that declared it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.engine.mcp.config_models.server_config import MCPServerConfig


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    """Validated server, with the context of the file that declared it."""

    name: str
    config: MCPServerConfig
    base_dir: Path
    source: Path

    @property
    def transport_kind(self) -> Literal['stdio', 'http']:
        return 'stdio' if self.config.command else 'http'

    @property
    def tool_prefix(self) -> str:
        """Tool prefix; ``None`` uses the server name, ``''`` adds no prefix."""
        prefix = self.config.prefix
        return self.name if prefix is None else prefix

    @property
    def cwd_path(self) -> Path | None:
        """Resolved ``cwd``: ``~`` expanded and paths relative to the config file."""
        raw = self.config.cwd
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        return path.resolve()
