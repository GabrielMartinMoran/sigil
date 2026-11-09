"""MCP meta-tools exposed to the model.

Each server's tools reach the agent as toolsets, but resources and
prompts are not tools: they are exposed here, with the same *progressive
disclosure* criterion as Agent Skills (list first, fetch later).
"""

from __future__ import annotations

from typing import Any

from src.engine.mcp.manager.manager import MCPManager
from src.engine.mcp.tool_models.prompt_info import PromptInfo
from src.engine.mcp.tool_models.prompt_result_info import PromptResultInfo
from src.engine.mcp.tool_models.resource_content import ResourceContent
from src.engine.mcp.tool_models.resource_info import ResourceInfo
from src.engine.mcp.tool_models.server_status import ServerStatus
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.compaction_policies import (
    GET_MCP_PROMPT_COMPACTOR,
    LIST_MCP_PROMPTS_COMPACTOR,
    LIST_MCP_RESOURCES_COMPACTOR,
    LIST_MCP_SERVERS_COMPACTOR,
    READ_MCP_RESOURCE_COMPACTOR,
)
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ToolGroup, ToolPermission
from src.engine.tooling.registrar import ToolRegistrar

_MCP = ToolPermission(groups=(ToolGroup.MCP,))
_MCP_SERVER = ToolPermission(groups=(ToolGroup.MCP,), resource='server')


def register_mcp_tools(
    agent: Any,
    manager: MCPManager,
    logger: ToolLogger | None = None,
    catalog: ToolCatalog | None = None,
) -> None:
    """Registers the MCP meta-tools on a pydantic-ai agent.

    With no configured servers it registers nothing: five empty tools would only
    waste context.
    """
    if not manager:
        return

    tools = ToolRegistrar(agent, logger, catalog)

    @tools.tool(permission=_MCP, compaction=LIST_MCP_SERVERS_COMPACTOR)
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

    @tools.tool(permission=_MCP_SERVER, compaction=LIST_MCP_RESOURCES_COMPACTOR)
    async def list_mcp_resources(server: str | None = None) -> list[ResourceInfo]:
        """List resources exposed by MCP servers.

        Resources are read-only data (files, records, documents) a server
        publishes under a URI. Read one with `read_mcp_resource`.

        Args:
            server: Optional server name to restrict the listing to one server.
        """
        return await manager.list_resources(server)

    @tools.tool(permission=_MCP_SERVER, compaction=READ_MCP_RESOURCE_COMPACTOR)
    async def read_mcp_resource(server: str, uri: str) -> ResourceContent:
        """Read an MCP resource by URI.

        Text content is returned as UTF-8; binary content is returned
        base64-encoded with `binary=true`.

        Args:
            server: Server name, as returned by `list_mcp_servers`.
            uri: Resource URI, as returned by `list_mcp_resources`.
        """
        return await manager.read_resource(server, uri)

    @tools.tool(permission=_MCP_SERVER, compaction=LIST_MCP_PROMPTS_COMPACTOR)
    async def list_mcp_prompts(server: str | None = None) -> list[PromptInfo]:
        """List prompts (reusable message templates) exposed by MCP servers.

        Resolve one with `get_mcp_prompt` to get its rendered messages.

        Args:
            server: Optional server name to restrict the listing to one server.
        """
        return await manager.list_prompts(server)

    @tools.tool(permission=_MCP_SERVER, compaction=GET_MCP_PROMPT_COMPACTOR)
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
