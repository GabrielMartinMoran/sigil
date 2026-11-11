"""The MCP layer, built after the window is up.

Starting Sigil used to pay for the whole MCP stack before the first paint: building
the manager imports ``pydantic_ai.mcp``, which drags in FastMCP and the MCP SDK and
costs half a second on a cold start. None of that is needed to draw the window and
show the last conversation, and the servers take their own time to answer anyway.

Two pieces make the deferral invisible to the rest of the application:

- ``PendingToolset`` — the agent's toolset until the real ones exist. It starts
  empty, so a turn that happens in that first second simply has no MCP tools yet,
  which is the state of a session whose servers are still starting.
- ``MCPGateway`` — the object the UI and the meta-tools hold. It answers from the
  config (whether there is anything configured) and waits for the manager before
  delegating, so nothing has to know that loading is still in progress.

The load itself runs on a worker thread but *finishes on the event loop*: the
toolset it builds has to be entered, used and closed by the loop that runs the
turns, so the thread only does the part that has no loop in it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.engine.mcp.pending import PendingToolset

if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset

    from src.engine.mcp.config_models.server_spec import MCPServerSpec
    from src.engine.mcp.manager.manager import MCPManager
    from src.engine.tooling.logging import ToolLogger


def build_manager(
    servers: Mapping[str, MCPServerSpec],
    logger: ToolLogger | None,
    shutdown_timeout: float | None = None,
) -> MCPManager:
    """Builds the manager and its toolsets. Blocking, and heavy: run it off-loop.

    The SDK's shutdown grace period is installed here rather than at startup so the
    ``mcp`` package is not imported on the startup path at all; the value comes from
    the composition root, which is the only place the environment is read.
    """
    from src.engine.mcp.manager.manager import MCPManager
    from src.engine.mcp.shutdown import DEFAULT_SHUTDOWN_TIMEOUT, apply_shutdown_timeout

    apply_shutdown_timeout(DEFAULT_SHUTDOWN_TIMEOUT if shutdown_timeout is None else shutdown_timeout)
    return MCPManager(servers, tool_logger=logger)


@dataclass
class MCPGateway:
    """Front end for the MCP layer, usable before it finished loading.

    It mirrors the manager's query surface, so the tools and the side panel hold
    this and never have to ask whether the servers are up yet. The configured
    count comes from the specs, which are already parsed: an app with no servers
    is falsy from the first line, exactly as it was before.
    """

    configured: int = 0
    config_errors: tuple[str, ...] = ()
    """Errors from parsing the config files. Known at startup, before any server runs."""
    toolsets: list[AbstractToolset[Any]] = field(default_factory=list)
    """The real toolsets, empty until the load finishes."""
    pending: PendingToolset = field(default_factory=PendingToolset)
    _manager: MCPManager | None = None
    _ready: asyncio.Event | None = None
    _load_errors: list[str] = field(default_factory=list)
    _overrides: dict[str, bool] = field(default_factory=dict)
    _closing: set[str] = field(default_factory=set)
    _reconciliation: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        self.pending.before_use = self.wait_for_reconciliation

    # --- readiness ----------------------------------------------------------

    def _event(self) -> asyncio.Event:
        # Created lazily: the gateway is built before any loop exists, and a
        # pre-created Event would bind to the wrong one.
        if self._ready is None:
            self._ready = asyncio.Event()
        return self._ready

    @property
    def loaded(self) -> bool:
        return self._manager is not None

    @property
    def manager(self) -> MCPManager | None:
        return self._manager

    async def wait(self) -> MCPManager | None:
        """Waits until the load finished. Returns `None` if it never will.

        A failed load sets the same event a successful one does, so a caller can
        never be left waiting on a manager that is not coming: it gets `None` and
        reports what it can.
        """
        await self._event().wait()
        await self.wait_for_reconciliation()
        return self._manager

    def install(self, manager: MCPManager) -> None:
        """Publishes the manager and the toolsets. Called once, from the loop."""
        self._manager = manager
        manager.set_overrides(self._overrides)
        self._refresh_toolsets()
        self._load_errors.extend(manager.errors)
        self._event().set()

    def set_overrides(self, overrides: Mapping[str, bool]) -> None:
        """Select the current session's servers, including while loading."""
        self._overrides = dict(overrides)
        if self._manager is not None:
            previously_enabled = {server.name for server in self._manager.servers.values() if server.enabled}
            self._manager.set_overrides(self._overrides)
            self._closing.update(
                server.name
                for server in self._manager.servers.values()
                if not server.enabled and server.name in previously_enabled
            )
            self._refresh_toolsets()
            self._schedule_reconciliation()

    def _schedule_reconciliation(self) -> None:
        if not self._closing or (self._reconciliation is not None and not self._reconciliation.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._reconciliation = loop.create_task(self._reconcile())

    async def _reconcile(self) -> None:
        while self._closing:
            names = self._closing.copy()
            self._closing.difference_update(names)
            assert self._manager is not None
            await asyncio.gather(*(self._manager.stop_disabled(name) for name in names))

    async def wait_for_reconciliation(self) -> None:
        self._schedule_reconciliation()
        task = self._reconciliation
        if task is not None:
            await asyncio.shield(task)

    async def stop_disabled(self, name: str) -> None:
        await self.wait_for_reconciliation()

    def _refresh_toolsets(self) -> None:
        assert self._manager is not None
        self.toolsets = list(self._manager.toolsets)
        # The agent holds the placeholder; filling it is what makes the tools
        # appear, and the swap is picked up by the next `get_tools` of a run.
        from pydantic_ai.toolsets import CombinedToolset

        self.pending.fill(CombinedToolset(self.toolsets))

    def fail(self, error: str) -> None:
        """Records a load that will not happen; waiters get an empty layer."""
        self._load_errors.append(error)
        self._event().set()

    @property
    def errors(self) -> tuple[str, ...]:
        """Config errors plus whatever the load reported, as one flat sequence."""
        return (*self.config_errors, *self._load_errors)

    # --- the manager's surface ---------------------------------------------

    def __bool__(self) -> bool:
        return self.configured > 0

    def __len__(self) -> int:
        return self.configured

    def get(self, name: str) -> Any:
        return self._manager.get(name) if self._manager is not None else None

    def counts(self) -> tuple[int, int]:
        if self._manager is None:
            return (0, 0)
        return self._manager.counts()

    async def describe(self, *, connect: bool = False) -> list[Any]:
        """Server state. Waits for the layer: this is the diagnostic path."""
        manager = await self.wait()
        if manager is None:
            return []
        return await manager.describe(connect=connect)

    async def list_resources(self, server: str | None = None) -> list[Any]:
        manager = await self.wait()
        return await manager.list_resources(server) if manager is not None else []

    async def read_resource(self, server: str, uri: str) -> Any:
        manager = await self.wait()
        if manager is None:
            raise RuntimeError('The MCP layer is not available in this session.')
        return await manager.read_resource(server, uri)

    async def list_prompts(self, server: str | None = None) -> list[Any]:
        manager = await self.wait()
        return await manager.list_prompts(server) if manager is not None else []

    async def get_prompt(self, server: str, name: str, arguments: dict[str, str] | None = None) -> Any:
        manager = await self.wait()
        if manager is None:
            raise RuntimeError('The MCP layer is not available in this session.')
        return await manager.get_prompt(server, name, arguments)

    async def aclose(self) -> None:
        """Releases the servers, if they ever loaded. Safe to call at any time."""
        manager = self._manager
        if manager is None:
            return
        await self.wait_for_reconciliation()
        await manager.aclose()


__all__ = ['MCPGateway', 'build_manager']
