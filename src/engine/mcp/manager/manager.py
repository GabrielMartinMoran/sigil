"""Orchestration of the configured MCP servers.

The manager gathers the lifecycle of the servers (construction, state and
diagnostics) and delegates to ``resources`` and ``prompts``, which are not tools but
are exposed to the model as meta-tools (see ``src.engine.mcp.tools``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.manager.prompts import get_prompt, list_prompts
from src.engine.mcp.manager.resources import list_resources, read_resource
from src.engine.mcp.server.mcp_server import MCPServer
from src.engine.mcp.server.naming import display_name, includes
from src.engine.mcp.server.toolset_wrapping import wrap_toolset
from src.engine.mcp.server.transport import create_toolset
from src.engine.mcp.tool_models.prompt_info import PromptInfo
from src.engine.mcp.tool_models.prompt_result_info import PromptResultInfo
from src.engine.mcp.tool_models.resource_content import ResourceContent
from src.engine.mcp.tool_models.resource_info import ResourceInfo
from src.engine.mcp.tool_models.server_status import ServerStatus
from src.engine.tooling.logging import ToolLogger


class MCPManager:
    """Builds the MCP toolsets and resolves resources and prompts."""

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

    # --- construction ------------------------------------------------------

    def _build(self, spec: MCPServerSpec, *, force: bool = False) -> MCPServer:
        if not spec.config.enabled and not force:
            return MCPServer(spec=spec)

        try:
            toolset = create_toolset(spec, self.tool_logger)
        except Exception as exc:  # noqa: BLE001 - a broken entry does not invalidate the rest
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

    def set_overrides(self, overrides: Mapping[str, bool]) -> None:
        """Apply the active session's choices without altering server configuration."""
        for name, server in self._servers.items():
            enabled = overrides.get(name, server.spec.config.enabled)
            was_enabled = server.enabled
            if enabled and server.toolset is None:
                server = self._build(server.spec, force=True)
                self._servers[name] = server
            elif enabled and not was_enabled:
                server.agent_toolset = wrap_toolset(
                    server.spec, server.toolset, on_error=lambda message, name=name: self._report(name, message)
                )
                server.error = None
            server.enabled_override = overrides.get(name)

    async def stop_disabled(self, name: str) -> None:
        """Disconnect an explicitly disabled server without discarding its configuration."""
        server = self._servers.get(name)
        if server is not None and server.toolset is not None:
            async with server.lifecycle_lock:
                if not server.enabled:
                    await self._close_unlocked(server)
                    server.tools = ()

    # --- queries -----------------------------------------------------------

    @property
    def servers(self) -> dict[str, MCPServer]:
        return dict(self._servers)

    @property
    def toolsets(self) -> list[AbstractToolset]:
        """Toolsets ready to hand to the agent."""
        return [
            server.agent_toolset
            for server in self._servers.values()
            if server.enabled and server.agent_toolset is not None
        ]

    def __len__(self) -> int:
        return len(self._servers)

    def __bool__(self) -> bool:
        return bool(self._servers)

    def get(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    def counts(self) -> tuple[int, int]:
        """``(ok, failed)`` among the enabled servers.

        "Ok" is optimistic: a server counts as healthy if its toolset was
        built and has not failed yet. ``describe(connect=True)`` is what
        confirms it actually responds.
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
        """State of each server; with ``connect`` it queries the real tools."""
        for server in self._servers.values():
            # With `connect` it always retries: `/mcp` is a diagnostic
            # command, and a server that already started may respond again.
            if connect and server.enabled and server.toolset is not None:
                await self._probe(server)
        return [server.status() for server in self._servers.values()]

    async def _probe(self, server: MCPServer) -> None:
        async with server.lifecycle_lock:
            if server.enabled:
                await self._probe_unlocked(server)

    async def _probe_unlocked(self, server: MCPServer) -> None:
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

    # --- shutdown -----------------------------------------------------------

    async def aclose(self) -> None:
        """Closes every server's transport, so the process can exit.

        The probe connects with ``keep_alive`` on, which leaves the session and the
        subprocess open on purpose: a turn later reuses them. Nothing closes them
        when the app quits, and the event loop then waits them out one by one — the
        reason closing Sigil took seconds.

        The closes run concurrently because they are independent waits, and the one
        that matters (a server ignoring the EOF) is bounded by the SDK's grace
        period, which `src.engine.mcp.shutdown` shortens.
        """
        closing = [self._close(server) for server in self._servers.values() if server.toolset is not None]
        if not closing:
            return
        await asyncio.gather(*closing, return_exceptions=True)

    async def _close(self, server: MCPServer) -> None:
        async with server.lifecycle_lock:
            await self._close_unlocked(server)

    async def _close_unlocked(self, server: MCPServer) -> None:
        assert server.toolset is not None
        try:
            await server.toolset.client.close()
        except Exception as exc:  # noqa: BLE001 - a server that will not close must not block the quit
            self._report(server.name, f'{type(exc).__name__}: {exc}')

    # --- resources and prompts ---------------------------------------------

    async def list_resources(self, server: str | None = None) -> list[ResourceInfo]:
        return await list_resources(self._servers, server)

    async def read_resource(self, server: str, uri: str) -> ResourceContent:
        return await read_resource(self._servers, server, uri)

    async def list_prompts(self, server: str | None = None) -> list[PromptInfo]:
        return await list_prompts(self._servers, server)

    async def get_prompt(
        self,
        server: str,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> PromptResultInfo:
        return await get_prompt(self._servers, server, name, arguments)
