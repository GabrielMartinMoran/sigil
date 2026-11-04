"""Result of reading the Sigil config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.engine.mcp.config_models.server_spec import MCPServerSpec


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """Result of reading the config: valid servers and errors."""

    servers: dict[str, MCPServerSpec]
    errors: tuple[str, ...] = ()
    sources: tuple[Path, ...] = ()

    def __len__(self) -> int:
        return len(self.servers)

    def enabled(self) -> dict[str, MCPServerSpec]:
        return {name: spec for name, spec in self.servers.items() if spec.config.enabled}
