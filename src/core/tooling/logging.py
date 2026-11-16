"""Emitting tool events towards injectable sinks.

The engine does not know the UI: it emits events to a ``ToolLogger`` and the UI plugs in a
sink that writes them where appropriate. A sink can be sync or async, and a
failure in it never interrupts the tool that triggered it.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace

from src.core.tooling.events import CURRENT_NODE_ID
from src.core.tooling.tool_call_event import ToolCallEvent
from src.core.tooling.tool_logger_hub import ToolLogger

"""Event sink. It can be sync or async; the return value is ignored."""


def emit_tool_event_sync(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emits from a sync tool; it schedules the async sinks on the current loop."""
    if logger is None:
        return
    if event.node_id is None:
        event = replace(event, node_id=CURRENT_NODE_ID.get())
    try:
        result = logger(event)
        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                loop.create_task(result)
    except Exception:  # noqa: BLE001, S110 - logging must never break a tool
        pass
