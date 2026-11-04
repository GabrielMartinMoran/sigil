"""Construcción y operación de los servidores MCP configurados.

Cada servidor declarado en ``.sigil/config.json`` se traduce a un
``MCPToolset`` de pydantic-ai (protocolo completo sobre el cliente de FastMCP)
y se envuelve en ``ResilientToolset`` para que un servidor caído no derribe el
turno.

El manager también es la puerta de entrada a resources y prompts, que no son
tools pero se exponen al modelo como meta-tools (ver ``tools.py``).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic_ai.mcp import MCPToolset, SSETransport, StdioTransport
from pydantic_ai.messages import BinaryContent
from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.models import (
    MCPConfigError,
    MCPServerSpec,
    PromptArgumentInfo,
    PromptInfo,
    PromptMessageInfo,
    PromptResultInfo,
    ResourceContent,
    ResourceInfo,
    ServerStatus,
)
from src.engine.mcp.resilient import ResilientToolset
from src.engine.tools.tool_manager import SELF_GUARD_ENV
from src.engine.tools.tool_registry import ToolCallEvent, ToolLogger, emit_tool_event, preview


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


def _display_name(prefix: str, tool_name: str) -> str:
    return f'{prefix}_{tool_name}' if prefix else tool_name


class MCPManager:
    """Arma los toolsets de MCP y resuelve resources y prompts."""

    def __init__(
        self,
        servers: Mapping[str, MCPServerSpec] | None = None,
        *,
        tool_logger: ToolLogger | None = None,
        on_error: Callable[[str, str], None] | None = None,
    ) -> None:
        self.tool_logger = tool_logger
        self.on_error = on_error
        self.errors: list[str] = []
        self._servers: dict[str, MCPServer] = {}
        for name, spec in (servers or {}).items():
            self._servers[name] = self._build(spec)

    # --- construcción -----------------------------------------------------

    def _build(self, spec: MCPServerSpec) -> MCPServer:
        if not spec.config.enabled:
            return MCPServer(spec=spec)

        try:
            toolset = self._create_toolset(spec)
        except Exception as exc:  # noqa: BLE001 - una entrada rota no invalida el resto
            message = f'{type(exc).__name__}: {exc}'
            self.errors.append(f'{spec.name}: {message}')
            return MCPServer(spec=spec, error=message)

        return MCPServer(spec=spec, toolset=toolset, agent_toolset=self._wrap(spec, toolset))

    def _create_toolset(self, spec: MCPServerSpec) -> MCPToolset:
        config = spec.config
        options: dict[str, Any] = {
            'id': spec.name,
            'include_instructions': config.include_instructions,
            'init_timeout': config.init_timeout,
            'read_timeout': config.read_timeout,
            'process_tool_call': self._tool_hook(spec),
        }

        if spec.transport_kind == 'stdio':
            cwd = spec.cwd_path
            transport = StdioTransport(
                command=config.command or '',
                args=list(config.args),
                # El guard de entorno impide que un server configurado con
                # `command: "sigil"` levante otra instancia del agente.
                env={**config.env, SELF_GUARD_ENV: '1'},
                cwd=str(cwd) if cwd is not None else None,
                keep_alive=config.keep_alive,
            )
            return MCPToolset(transport, **options)

        url = config.url or ''
        headers = dict(config.headers) or None
        if config.transport == 'sse':
            return MCPToolset(SSETransport(url, headers=headers), **options)
        return MCPToolset(url, headers=headers, **options)

    def _wrap(self, spec: MCPServerSpec, toolset: MCPToolset) -> AbstractToolset:
        wrapped: AbstractToolset = toolset
        if spec.config.include_tools or spec.config.exclude_tools:
            wrapped = wrapped.filtered(self._filter(spec))
        prefix = spec.tool_prefix
        if prefix:
            wrapped = wrapped.prefixed(prefix)
        return ResilientToolset(wrapped, on_error=lambda message: self._report(spec.name, message))

    def _report(self, server: str, message: str) -> None:
        self.errors.append(f'{server}: {message}')
        if self.on_error is not None:
            self.on_error(server, message)

    @staticmethod
    def _includes(spec: MCPServerSpec, tool_name: str) -> bool:
        include = spec.config.include_tools
        if include:
            return tool_name in include
        return tool_name not in spec.config.exclude_tools

    def _filter(self, spec: MCPServerSpec) -> Callable[[Any, Any], bool]:
        def predicate(_ctx: Any, tool_def: Any) -> bool:
            return self._includes(spec, tool_def.name)

        return predicate

    def _tool_hook(self, spec: MCPServerSpec) -> Callable[..., Any]:
        """Loguea las llamadas a tools MCP en el hub de la UI.

        Las tools que vienen de un toolset no pasan por ``ToolRegistrar``, así
        que este hook es el único punto donde se pueden emitir eventos.
        """

        async def process(ctx: Any, call_tool: Any, name: str, args: dict[str, Any]) -> Any:
            display = _display_name(spec.tool_prefix, name)
            await emit_tool_event(
                self.tool_logger,
                ToolCallEvent(tool_name=display, phase='start', args=dict(args)),
            )
            started = time.perf_counter()
            try:
                result = await call_tool(name, args)
            except Exception as exc:
                await emit_tool_event(
                    self.tool_logger,
                    ToolCallEvent(
                        tool_name=display,
                        phase='error',
                        duration_ms=round((time.perf_counter() - started) * 1000, 2),
                        error=f'{type(exc).__name__}: {exc}',
                    ),
                )
                raise
            await emit_tool_event(
                self.tool_logger,
                ToolCallEvent(
                    tool_name=display,
                    phase='success',
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    result_preview=preview(result),
                ),
            )
            return result

        return process

    # --- consultas --------------------------------------------------------

    @property
    def servers(self) -> dict[str, MCPServer]:
        return dict(self._servers)

    @property
    def toolsets(self) -> list[AbstractToolset]:
        """Toolsets listos para pasarle al agente."""
        return [server.agent_toolset for server in self._servers.values() if server.agent_toolset is not None]

    def __len__(self) -> int:
        return len(self._servers)

    def __bool__(self) -> bool:
        return bool(self._servers)

    def get(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    def _require(self, name: str) -> MCPServer:
        server = self._servers.get(name)
        if server is None:
            available = ', '.join(sorted(self._servers)) or 'none'
            raise MCPConfigError(f"Unknown MCP server '{name}'. Configured servers: {available}.")
        if server.toolset is None:
            state = 'disabled' if not server.enabled else 'not available'
            raise MCPConfigError(f"MCP server '{name}' is {state}.")
        return server

    def _targets(self, server: str | None) -> list[MCPServer]:
        if server is not None:
            return [self._require(server)]
        return [item for item in self._servers.values() if item.toolset is not None]

    def counts(self) -> tuple[int, int]:
        """``(andando, con error)`` entre los servidores habilitados.

        "Andando" es optimista: un servidor cuenta como sano si su toolset se
        construyó y no falló todavía. `describe(connect=True)` es lo que confirma
        que responde de verdad.
        """
        ok = failed = 0
        for server in self._servers.values():
            if not server.enabled:
                continue
            if server.error:
                failed += 1
            elif server.agent_toolset is not None:
                ok += 1
        return ok, failed

    async def describe(self, *, connect: bool = False) -> list[ServerStatus]:
        """Estado de cada servidor; con ``connect`` consulta las tools reales."""
        for server in self._servers.values():
            # Con `connect` se reintenta siempre: `/mcp` es un comando de
            # diagnóstico, y un servidor que ya arrancó puede volver a responder.
            if connect and server.toolset is not None:
                await self._probe(server)
        return [server.status() for server in self._servers.values()]

    async def _probe(self, server: MCPServer) -> None:
        assert server.toolset is not None
        try:
            tools = await server.toolset.list_tools()
        except Exception as exc:  # noqa: BLE001
            server.error = f'{type(exc).__name__}: {exc}'
            server.tools = ()
        else:
            server.error = None
            server.tools = tuple(
                _display_name(server.spec.tool_prefix, tool.name)
                for tool in tools
                if self._includes(server.spec, tool.name)
            )

    # --- resources y prompts ---------------------------------------------

    async def list_resources(self, server: str | None = None) -> list[ResourceInfo]:
        found: list[ResourceInfo] = []
        for item in self._targets(server):
            assert item.toolset is not None
            try:
                resources = await item.toolset.list_resources()
            except Exception as exc:  # noqa: BLE001
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

    async def read_resource(self, server: str, uri: str) -> ResourceContent:
        item = self._require(server)
        assert item.toolset is not None
        result = await item.toolset.read_resource(uri)
        return _to_content(item.name, uri, result)

    async def list_prompts(self, server: str | None = None) -> list[PromptInfo]:
        found: list[PromptInfo] = []
        for item in self._targets(server):
            assert item.toolset is not None
            try:
                prompts = await item.toolset.list_prompts()
            except Exception as exc:  # noqa: BLE001
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
        self,
        server: str,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> PromptResultInfo:
        item = self._require(server)
        assert item.toolset is not None
        result = await item.toolset.get_prompt(name, arguments)
        return PromptResultInfo(
            server=item.name,
            name=name,
            description=result.description or None,
            messages=[
                PromptMessageInfo(role=str(message.role), content=_content_text(message.content))
                for message in result.messages
            ],
        )


def _content_text(content: Any) -> str:
    text = getattr(content, 'content', None)
    return text if isinstance(text, str) else str(content)


def _to_content(server: str, uri: str, result: Any) -> ResourceContent:
    """Normaliza ``str | BinaryContent | list[...]`` a un único payload."""
    if isinstance(result, list):
        return _join_contents(server, uri, result)
    if isinstance(result, BinaryContent):
        return ResourceContent(
            server=server,
            uri=uri,
            mime_type=result.media_type,
            binary=True,
            encoding='base64',
            content=base64.b64encode(result.data).decode('ascii'),
        )
    return ResourceContent(server=server, uri=uri, content=str(result))


def _join_contents(server: str, uri: str, parts: list[Any]) -> ResourceContent:
    if len(parts) == 1:
        return _to_content(server, uri, parts[0])

    chunks: list[str] = []
    has_binary = False
    media_type: str | None = None
    for part in parts:
        if isinstance(part, BinaryContent):
            has_binary = True
            media_type = media_type or part.media_type
            encoded = base64.b64encode(part.data).decode('ascii')
            chunks.append(f'[binary {part.media_type}, base64]\n{encoded}')
        else:
            chunks.append(str(part))
    return ResourceContent(
        server=server,
        uri=uri,
        mime_type=media_type,
        binary=has_binary,
        encoding='base64' if has_binary else 'utf-8',
        content='\n\n'.join(chunks),
    )


__all__ = ['MCPManager', 'MCPServer']
