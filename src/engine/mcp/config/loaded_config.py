"""Result of reading the Sigil config."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.engine.agents.model_config import AgentModelConfig
from src.engine.mcp.config_models.server_spec import MCPServerSpec


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """Result of reading the config: valid servers and errors."""

    servers: dict[str, MCPServerSpec]
    agents: dict[str, AgentModelConfig] = field(default_factory=dict)
    invalid_agents: frozenset[str] = frozenset()
    default_model: AgentModelConfig | None = None
    """The model a conversation starts on when nothing else decides.

    Set by `default_model` in the Sigil config, with the project file winning over the
    user's global one. ``agents.<name>`` beats it, and it beats the last model used.
    """
    errors: tuple[str, ...] = ()
    sources: tuple[Path, ...] = ()

    def __len__(self) -> int:
        return len(self.servers)

    def enabled(self) -> dict[str, MCPServerSpec]:
        return {name: spec for name, spec in self.servers.items() if spec.config.enabled}
