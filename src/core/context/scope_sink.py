"""The ScopeSink value of the context layer."""

from __future__ import annotations

from collections.abc import Mapping

from src.core.context.scope_tracker import ScopeTracker


class ScopeSink:
    """`ToolLoggerHub` sink that feeds the tracker on every call.

    It hooks into the hub instead of each tool: that way it also sees the MCP tools and
    the ones registered later, without touching their signatures.
    """

    def __init__(self, tracker: ScopeTracker) -> None:
        self._tracker = tracker

    def __call__(self, event: object) -> None:
        if getattr(event, 'phase', None) != 'start':
            return
        tool_name = getattr(event, 'tool_name', '')
        args = getattr(event, 'args', None)
        if isinstance(tool_name, str) and isinstance(args, Mapping):
            self._tracker.observe(tool_name, args)
