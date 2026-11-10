"""Lazy toolset for local extensions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from sigil_tools import ToolContext
from src.engine.extensions.manager import ExtensionManager
from src.engine.extensions.registration import register_extensions
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.outputs import unregister_output


class ExtensionToolset(AbstractToolset[Any]):
    """One lazily initialized set of tools for a workspace."""

    def __init__(
        self,
        *,
        workspace: Callable[[], Path],
        catalog: ToolCatalog,
        authorize: Callable[[Path], Awaitable[bool]],
        global_dir: Path | None = None,
        logger: ToolLogger | None = None,
        context_factory: Callable[[RunContext[Any]], ToolContext] | None = None,
        notice: Callable[[str], None] | None = None,
    ) -> None:
        self._workspace = workspace
        self._catalog = catalog
        self._authorize = authorize
        self._global_dir = global_dir
        self._logger = logger
        self._context_factory = context_factory
        self._notice = notice
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._lock = asyncio.Lock()
        self._manager: ExtensionManager | None = None
        self._toolset: FunctionToolset[Any] | None = None

    @property
    def id(self) -> str | None:
        return None

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        async with self._lock:
            if self._manager is not None and self._manager.workspace != self._workspace().resolve():
                await self._close()
            if self._manager is None:
                await self._load()
            assert self._toolset is not None
            return await self._toolset.get_tools(ctx)

    async def _load(self) -> None:
        workspace = self._workspace().resolve()
        project_dir = workspace / '.sigil' / 'tools'
        manager = ExtensionManager(workspace, global_dir=self._global_dir)
        has_project = project_dir.resolve() != manager.global_dir.resolve() and bool(
            ExtensionManager._directories(project_dir)
        )
        manager.trust_project = has_project and await self._authorize(workspace)
        await manager.load()
        toolset: FunctionToolset[Any] = FunctionToolset()
        register_extensions(
            toolset, manager, catalog=self._catalog, logger=self._logger, context_factory=self._context_factory
        )
        self._manager = manager
        self._toolset = toolset
        self._report(manager.errors)

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        async with self._lock:
            if self._toolset is None or self._manager is None:
                raise LookupError(f'External tool {name!r} is not available')
            if self._manager.workspace != self._workspace().resolve():
                raise RuntimeError('Workspace changed before external tool execution')
            current = self._toolset
            if tool.toolset is not current:
                raise RuntimeError('External tool was unloaded after this request was prepared')
            self._active += 1
            self._idle.clear()
        try:
            return await current.call_tool(name, tool_args, ctx, tool)
        finally:
            self._active -= 1
            if not self._active:
                self._idle.set()

    def _report(self, errors: list[str]) -> None:
        if self._notice is not None:
            for error in errors:
                self._notice(f'External tools: {error}')

    async def close(self) -> None:
        async with self._lock:
            await self._close()

    async def _close(self) -> None:
        await self._idle.wait()
        if self._manager is not None:
            manager = self._manager
            previous_errors = len(manager.errors)
            await manager.close()
            self._report(manager.errors[previous_errors:])
            for name, declaration in manager.registered_declarations.items():
                if self._catalog.get(name) is declaration:
                    self._catalog.remove(name)
                    unregister_output(name)
        self._manager = None
        self._toolset = None
