"""Construcción de un servidor MCP: transporte, filtros, prefijo y resiliencia.

Cada servidor declarado en ``.sigil/config.json`` se traduce a un ``MCPToolset``
de pydantic-ai y se envuelve en ``ResilientToolset`` para que un servidor caído
no derribe el turno.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai.mcp import MCPToolset, SSETransport, StdioTransport
from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.config_models import MCPServerSpec
from src.engine.mcp.resilient import ResilientToolset
from src.engine.mcp.tool_models import ServerStatus
from src.engine.shell.guard import SELF_GUARD_ENV
from src.engine.tooling.events import ToolCallEvent, preview
from src.engine.tooling.logging import ToolLogger, emit_tool_event


def display_name(prefix: str, tool_name: str) -> str:
    """Nombre de la tool tal como lo ve el modelo."""
    return f'{prefix}_{tool_name}' if prefix else tool_name


def includes(spec: MCPServerSpec, tool_name: str) -> bool:
    """Si la tool pasa el allowlist/denylist del servidor."""
    include = spec.config.include_tools
    if include:
        return tool_name in include
    return tool_name not in spec.config.exclude_tools


def _tool_hook(spec: MCPServerSpec, logger: ToolLogger | None) -> Callable[..., Any]:
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


def _stdio_transport(spec: MCPServerSpec) -> StdioTransport:
    """Transporte stdio, con el guard de entorno en el proceso hijo."""
    config = spec.config
    cwd = spec.cwd_path
    return StdioTransport(
        command=config.command or '',
        args=list(config.args),
        # El guard de entorno impide que un server configurado con
        # `command: "sigil"` levante otra instancia del agente.
        env={**config.env, SELF_GUARD_ENV: '1'},
        cwd=str(cwd) if cwd is not None else None,
        keep_alive=config.keep_alive,
    )


def create_toolset(spec: MCPServerSpec, logger: ToolLogger | None) -> MCPToolset:
    """Crea el ``MCPToolset`` del servidor, sin filtros ni prefijo."""
    config = spec.config
    options: dict[str, Any] = {
        'id': spec.name,
        'include_instructions': config.include_instructions,
        'init_timeout': config.init_timeout,
        'read_timeout': config.read_timeout,
        'process_tool_call': _tool_hook(spec, logger),
    }

    if spec.transport_kind == 'stdio':
        return MCPToolset(_stdio_transport(spec), **options)

    url = config.url or ''
    headers = dict(config.headers) or None
    if config.transport == 'sse':
        return MCPToolset(SSETransport(url, headers=headers), **options)
    return MCPToolset(url, headers=headers, **options)


def _filter(spec: MCPServerSpec) -> Callable[[Any, Any], bool]:
    def predicate(_ctx: Any, tool_def: Any) -> bool:
        return includes(spec, tool_def.name)

    return predicate


def wrap_toolset(spec: MCPServerSpec, toolset: MCPToolset, on_error: Callable[[str], None]) -> AbstractToolset:
    """Aplica filtros, prefijo y degradación controlada al toolset."""
    wrapped: AbstractToolset = toolset
    if spec.config.include_tools or spec.config.exclude_tools:
        wrapped = wrapped.filtered(_filter(spec))
    if spec.tool_prefix:
        wrapped = wrapped.prefixed(spec.tool_prefix)
    return ResilientToolset(wrapped, on_error=on_error)


@dataclass
class MCPServer:
    """Servidor MCP configurado y su estado en runtime."""

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
        """Tiene toolset y no falló al construirse."""
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
