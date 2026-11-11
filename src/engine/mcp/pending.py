"""The MCP layer, built off the startup path.

Starting Sigil used to pay for the whole MCP stack before the first paint: the
``pydantic_ai.mcp`` import chain (FastMCP, the MCP SDK, its auth extensions) costs
over half a second on a cold start, and the agent cannot be built until its
toolsets exist. None of that is needed to draw a window and show the last
conversation, and the servers take their own time to answer anyway.

So the agent is built with a *placeholder* toolset that starts with no tools and
is filled in later, when the load finishes on a worker thread. The placeholder is
an ``AbstractToolset`` of our own instead of a ``DynamicToolset`` because the
latter still lives in ``pydantic_ai.toolsets`` and only defers *evaluating* a
factory: importing the module is what costs the time, so the object the agent
holds must not need it at all.

Nothing is lost while the load runs: a turn started in that window simply has no
MCP tools yet, which is exactly the state of a session whose servers are still
starting. The meta-tools are registered with the rest, on the same thread.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Self

from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool


class PendingToolset(AbstractToolset[Any]):
    """A toolset that is empty until another one replaces it.

    It answers every request from whatever it currently holds, so the swap is
    visible to a run already in flight: ``get_tools`` is called per step, not once
    per run.
    """

    def __init__(self, toolset: AbstractToolset[Any] | None = None) -> None:
        self._toolset = toolset
        self.before_use: Callable[[], Awaitable[None]] | None = None

    @property
    def id(self) -> str | None:
        # No id: this is a role the application fills, not a registered toolset.
        return None

    @property
    def loaded(self) -> bool:
        return self._toolset is not None

    def fill(self, toolset: AbstractToolset[Any]) -> None:
        """Installs the real toolset. Called once, from the loading worker."""
        self._toolset = toolset

    async def __aenter__(self) -> Self:
        if self.before_use is not None:
            await self.before_use()
        if self._toolset is not None:
            await self._toolset.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> bool | None:
        if self._toolset is not None:
            return await self._toolset.__aexit__(*args)
        return None

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        if self.before_use is not None:
            await self.before_use()
        if self._toolset is None:
            return {}
        return await self._toolset.get_tools(ctx)

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: ToolsetTool[Any]) -> Any:
        if self.before_use is not None:
            await self.before_use()
        if self._toolset is None:
            raise LookupError(f'No MCP tool is available yet: {name!r} was called before its server was loaded.')
        return await self._toolset.call_tool(name, tool_args, ctx, tool)


__all__ = ['PendingToolset']
