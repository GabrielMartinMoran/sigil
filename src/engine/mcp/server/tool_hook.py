"""Hook que emite eventos de tool para un servidor MCP."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.server.naming import display_name
from src.engine.tooling.events import ToolCallEvent, preview
from src.engine.tooling.logging import ToolLogger, emit_tool_event


def tool_hook(spec: MCPServerSpec, logger: ToolLogger | None) -> Callable[..., Any]:
    """Loguea las llamadas a tools MCP en el hub de la UI.

    Las tools que vienen de un toolset no pasan por ``ToolRegistrar``, así que
    este hook es el único punto donde se pueden emitir eventos.
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
