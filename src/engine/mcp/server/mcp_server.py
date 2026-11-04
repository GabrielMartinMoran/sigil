"""Configured MCP server and its runtime state."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.tool_models.server_status import ServerStatus


@dataclass
class MCPServer:
    """Configured MCP server and its runtime state."""

    spec: MCPServerSpec
    toolset: MCPToolset | None = None
    agent_toolset: AbstractToolset | None = None
    error: str | None = None
    tools: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def enabled(self) -> bool:
        return self.spec.config.enabled

    @property
    def active(self) -> bool:
        """It has a toolset and did not fail while being built."""
        return self.agent_toolset is not None and self.error is None

    def status(self) -> ServerStatus:
        return ServerStatus(
            name=self.name,
            transport=self.spec.transport_kind,
            enabled=self.enabled,
            prefix=self.spec.tool_prefix,
            tools=list(self.tools),
            error=self.error,
            source=str(self.spec.source),
        )
