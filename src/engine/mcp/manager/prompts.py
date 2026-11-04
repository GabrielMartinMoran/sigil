"""Consulta y resolución de prompts de los servidores MCP."""

from __future__ import annotations

from src.engine.mcp.content import content_text
from src.engine.mcp.manager.selection import ServerMap, require, targets
from src.engine.mcp.tool_models.prompt_argument_info import PromptArgumentInfo
from src.engine.mcp.tool_models.prompt_info import PromptInfo
from src.engine.mcp.tool_models.prompt_message_info import PromptMessageInfo
from src.engine.mcp.tool_models.prompt_result_info import PromptResultInfo


async def list_prompts(servers: ServerMap, server: str | None = None) -> list[PromptInfo]:
    """Prompts expuestos por los servidores alcanzados."""
    found: list[PromptInfo] = []
    for item in targets(servers, server):
        assert item.toolset is not None
        try:
            prompts = await item.toolset.list_prompts()
        except Exception as exc:  # noqa: BLE001 - un servidor caído no anula el resto
            item.error = f'{type(exc).__name__}: {exc}'
            continue
        found.extend(
            PromptInfo(
                server=item.name,
                name=prompt.name,
                description=prompt.description or None,
                arguments=[
                    PromptArgumentInfo(
                        name=argument.name,
                        description=argument.description or None,
                        required=bool(argument.required),
                    )
                    for argument in (prompt.arguments or [])
                ],
            )
            for prompt in prompts
        )
    return found


async def get_prompt(
    servers: ServerMap,
    server: str,
    name: str,
    arguments: dict[str, str] | None = None,
) -> PromptResultInfo:
    """Resuelve un prompt en sus mensajes ya renderizados."""
    item = require(servers, server)
    assert item.toolset is not None
    result = await item.toolset.get_prompt(name, arguments)
    return PromptResultInfo(
        server=item.name,
        name=name,
        description=result.description or None,
        messages=[
            PromptMessageInfo(role=str(message.role), content=content_text(message.content))
            for message in result.messages
        ],
    )
