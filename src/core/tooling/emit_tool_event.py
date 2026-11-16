"""The emit_tool_event helper of the tooling layer."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from src.core.tooling.events import CURRENT_NODE_ID
from src.core.tooling.tool_call_event import ToolCallEvent

ToolLogger = Callable[[ToolCallEvent], Any]


async def emit_tool_event(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emits an event to a sink, ignoring any logging failure.

    Public for integrations that do not go through ``wrap_tool``, for example the
    tools of an MCP server, which run inside a toolset.
    """
    if logger is None:
        return
    if event.node_id is None:
        event = replace(event, node_id=CURRENT_NODE_ID.get())
    try:
        result = logger(event)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001, S110 - logging must never break a tool
        pass
