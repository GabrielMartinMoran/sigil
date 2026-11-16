"""The ToolLoggerHub value of the tooling layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.core.tooling.emit_tool_event import ToolLogger, emit_tool_event
from src.core.tooling.tool_call_event import ToolCallEvent


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
