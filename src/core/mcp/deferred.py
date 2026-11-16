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

from collections.abc import Mapping
from typing import TYPE_CHECKING

from src.core.mcp.mcp_gateway import MCPGateway

if TYPE_CHECKING:
    from src.core.mcp.config_models.mcp_server_spec import MCPServerSpec
    from src.core.mcp.manager.mcp_manager import MCPManager
    from src.core.tooling.logging import ToolLogger


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
    from src.core.mcp.manager.mcp_manager import MCPManager
    from src.core.mcp.shutdown import DEFAULT_SHUTDOWN_TIMEOUT, apply_shutdown_timeout

    apply_shutdown_timeout(DEFAULT_SHUTDOWN_TIMEOUT if shutdown_timeout is None else shutdown_timeout)
    return MCPManager(servers, tool_logger=logger)


__all__ = ['MCPGateway', 'build_manager']
