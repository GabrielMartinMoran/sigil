"""Emitting tool events towards injectable sinks.

The engine does not know the UI: it emits events to a ``ToolLogger`` and the UI plugs in a
sink that writes them where appropriate. A sink can be sync or async, and a
failure in it never interrupts the tool that triggered it.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from typing import Any

from src.engine.tooling.events import ToolCallEvent

ToolLogger = Callable[[ToolCallEvent], Any]
"""Event sink. It can be sync or async; the return value is ignored."""


async def emit_tool_event(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emits an event to a sink, ignoring any logging failure.

    Public for integrations that do not go through ``wrap_tool``, for example the
    tools of an MCP server, which run inside a toolset.
    """
    if logger is None:
        return
    try:
        result = logger(event)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001, S110 - logging must never break a tool
        pass


def emit_tool_event_sync(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emits from a sync tool; it schedules the async sinks on the current loop."""
    if logger is None:
        return
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


class ToolLoggerHub:
    """Fan-out of tool events towards one or several sinks.

    It is the injection point between engine and UI: the composition root creates the hub,
    passes it to the tools and to the app, and the app adds its sink on mount.
    """

    def __init__(self, sinks: Sequence[ToolLogger] = ()) -> None:
        self._sinks: list[ToolLogger] = list(sinks)

    def add_sink(self, sink: ToolLogger) -> None:
        if sink not in self._sinks:
            self._sinks.append(sink)

    def remove_sink(self, sink: ToolLogger) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    def __call__(self, event: ToolCallEvent) -> Any:
        return self.emit(event)

    async def emit(self, event: ToolCallEvent) -> None:
        for sink in tuple(self._sinks):
            await emit_tool_event(sink, event)
