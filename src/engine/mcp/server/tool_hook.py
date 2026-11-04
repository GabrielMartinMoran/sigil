"""Hook that emits tool events for an MCP server."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.server.naming import display_name
from src.engine.tooling.events import ToolCallEvent, preview
from src.engine.tooling.logging import ToolLogger, emit_tool_event


def tool_hook(spec: MCPServerSpec, logger: ToolLogger | None) -> Callable[..., Any]:
    """Logs MCP tool calls into the UI hub.

    Tools coming from a toolset do not go through ``ToolRegistrar``, so
    this hook is the only place where events can be emitted.
    """

    async def process(_ctx: Any, call_tool: Any, name: str, args: dict[str, Any]) -> Any:
        display = display_name(spec.tool_prefix, name)
        await emit_tool_event(logger, ToolCallEvent(tool_name=display, phase='start', args=dict(args)))
        started = time.perf_counter()
        try:
            result = await call_tool(name, args)
        except Exception as exc:
            await emit_tool_event(
                logger,
                ToolCallEvent(
                    tool_name=display,
                    phase='error',
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    error=f'{type(exc).__name__}: {exc}',
                ),
            )
            raise
        await emit_tool_event(
            logger,
            ToolCallEvent(
                tool_name=display,
                phase='success',
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                result_preview=preview(result),
            ),
        )
        return result

    return process
