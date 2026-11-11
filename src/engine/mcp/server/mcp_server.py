"""Configured MCP server and its runtime state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.tool_models.server_status import ServerStatus

if TYPE_CHECKING:
    from pydantic_ai.mcp import MCPToolset


@dataclass
class MCPServer:
    """Configured MCP server and its runtime state."""

    spec: MCPServerSpec
    # The annotation is a string at runtime (`from __future__ import annotations`),
    # so naming `MCPToolset` does not make this module import `pydantic_ai.mcp` —
    # and that import is half the cold start.
    toolset: MCPToolset | None = None
    agent_toolset: AbstractToolset | None = None
    error: str | None = None
    tools: tuple[str, ...] = ()
    enabled_override: bool | None = None
    lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def enabled(self) -> bool:
        return self.spec.config.enabled if self.enabled_override is None else self.enabled_override

    @property
    def active(self) -> bool:
        """It has a toolset and did not fail while being built."""
        return self.enabled and self.agent_toolset is not None and self.error is None

    def status(self) -> ServerStatus:
        return ServerStatus(
            name=self.name,
            transport=self.spec.transport_kind,
            enabled=self.enabled,
            prefix=self.spec.tool_prefix,
            tools=list(self.tools) if self.enabled else [],
            error=self.error,
            source=str(self.spec.source),
        )
