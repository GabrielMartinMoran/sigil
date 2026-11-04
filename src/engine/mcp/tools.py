"""Meta-tools de MCP expuestas al modelo.

Las tools de cada servidor llegan al agente como toolsets, pero resources y
prompts no son tools: se exponen acá, con el mismo criterio de *progressive
disclosure* que los Agent Skills (listar primero, traer después).
"""

from __future__ import annotations

from typing import Any

from src.engine.mcp.manager import MCPManager
from src.engine.mcp.tool_models import (
    PromptInfo,
    PromptResultInfo,
    ResourceContent,
    ResourceInfo,
    ServerStatus,
)
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.registrar import ToolRegistrar


def register_mcp_tools(agent: Any, manager: MCPManager, logger: ToolLogger | None = None) -> None:
    """Registra las meta-tools de MCP en un agente de pydantic-ai.

    Si no hay servidores configurados no registra nada: cinco tools vacías sólo
    gastarían contexto.
    """
    if not manager:
        return

    tools = ToolRegistrar(agent, logger)

    @tools.tool
    async def list_mcp_servers(connect: bool = False) -> list[ServerStatus]:
        """List the configured MCP servers and their state.

        Use this to diagnose MCP: which servers are configured, whether they are
        enabled, which config file declared them, and the last error seen.

        Args:
            connect: When true, connect to each server and report the tools it
                exposes. This can take a few seconds per server (cold start), so
                leave it false unless you need the tool inventory.
        """
        return await manager.describe(connect=connect)

    @tools.tool
    async def list_mcp_resources(server: str | None = None) -> list[ResourceInfo]:
        """List resources exposed by MCP servers.

        Resources are read-only data (files, records, documents) a server
        publishes under a URI. Read one with `read_mcp_resource`.

        Args:
            server: Optional server name to restrict the listing to one server.
        """
        return await manager.list_resources(server)

    @tools.tool
    async def read_mcp_resource(server: str, uri: str) -> ResourceContent:
        """Read an MCP resource by URI.

        Text content is returned as UTF-8; binary content is returned
        base64-encoded with `binary=true`.

        Args:
            server: Server name, as returned by `list_mcp_servers`.
            uri: Resource URI, as returned by `list_mcp_resources`.
        """
        return await manager.read_resource(server, uri)

    @tools.tool
    async def list_mcp_prompts(server: str | None = None) -> list[PromptInfo]:
        """List prompts (reusable message templates) exposed by MCP servers.

        Resolve one with `get_mcp_prompt` to get its rendered messages.

        Args:
            server: Optional server name to restrict the listing to one server.
        """
        return await manager.list_prompts(server)

    @tools.tool
    async def get_mcp_prompt(
        server: str,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> PromptResultInfo:
        """Resolve an MCP prompt into its rendered messages.

        Args:
            server: Server name, as returned by `list_mcp_servers`.
            name: Prompt name, as returned by `list_mcp_prompts`.
            arguments: Prompt arguments, if the prompt declares any.
        """
        return await manager.get_prompt(server, name, arguments)


__all__ = ['register_mcp_tools']
