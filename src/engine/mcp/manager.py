"""Orquestación de los servidores MCP configurados.

El manager arma los toolsets (delegando la construcción en ``server``), reporta
su estado y es la puerta de entrada a resources y prompts, que no son tools pero
se exponen al modelo como meta-tools (ver ``tools``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.config_models import MCPConfigError, MCPServerSpec
from src.engine.mcp.content import content_text, to_content
from src.engine.mcp.server import MCPServer, create_toolset, display_name, includes, wrap_toolset
from src.engine.mcp.tool_models import (
    PromptArgumentInfo,
    PromptInfo,
    PromptMessageInfo,
    PromptResultInfo,
    ResourceContent,
    ResourceInfo,
    ServerStatus,
)
from src.engine.tooling.logging import ToolLogger


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
            toolset = create_toolset(spec, self.tool_logger)
        except Exception as exc:  # noqa: BLE001 - una entrada rota no invalida el resto
            message = f'{type(exc).__name__}: {exc}'
            self.errors.append(f'{spec.name}: {message}')
            return MCPServer(spec=spec, error=message)

        return MCPServer(
            spec=spec,
            toolset=toolset,
            agent_toolset=wrap_toolset(spec, toolset, on_error=lambda message: self._report(spec.name, message)),
        )

    def _report(self, server: str, message: str) -> None:
        self.errors.append(f'{server}: {message}')
        if self.on_error is not None:
            self.on_error(server, message)

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
        construyó y no falló todavía. ``describe(connect=True)`` es lo que
        confirma que responde de verdad.
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
            return

        server.error = None
        server.tools = tuple(
            display_name(server.spec.tool_prefix, tool.name) for tool in tools if includes(server.spec, tool.name)
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
        return to_content(item.name, uri, await item.toolset.read_resource(uri))

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
                PromptMessageInfo(role=str(message.role), content=content_text(message.content))
                for message in result.messages
            ],
        )
