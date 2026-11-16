"""The MCP layer as the sidebar paints it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MCPServerState:
    """State of an MCP server for the panel."""

    name: str
    ok: bool
