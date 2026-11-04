"""Consulta de resources de los servidores MCP."""

from __future__ import annotations

from src.engine.mcp.content import to_content
from src.engine.mcp.manager.selection import ServerMap, require, targets
from src.engine.mcp.tool_models.resource_content import ResourceContent
from src.engine.mcp.tool_models.resource_info import ResourceInfo


async def list_resources(servers: ServerMap, server: str | None = None) -> list[ResourceInfo]:
    """Resources publicados por los servidores alcanzados."""
    found: list[ResourceInfo] = []
    for item in targets(servers, server):
        assert item.toolset is not None
        try:
            resources = await item.toolset.list_resources()
        except Exception as exc:  # noqa: BLE001 - un servidor caído no anula el resto
            item.error = f'{type(exc).__name__}: {exc}'
            continue
        found.extend(
            ResourceInfo(
                server=item.name,
                uri=str(resource.uri),
                name=resource.name or None,
                mime_type=resource.mime_type or None,
                description=resource.description or None,
            )
            for resource in resources
        )
    return found


async def read_resource(servers: ServerMap, server: str, uri: str) -> ResourceContent:
    """Contenido de un resource, normalizado a un payload único."""
    item = require(servers, server)
    assert item.toolset is not None
    return to_content(item.name, uri, await item.toolset.read_resource(uri))
